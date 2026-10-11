'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Globe, RefreshCw, Send, X, CheckCircle, XCircle, Clock,
  Loader2, ChevronDown, ChevronRight, Zap, Shield, MessageSquare,
  Copy, Play, Ban, Eye, Server, Code, AlertCircle, Network,
  ArrowRight, RotateCcw, FileText, Search, Activity,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

// ── Types ───────────────────────────────────────────────────

interface AgentSkill {
  id: string;
  name: string;
  description: string;
  tags: string[];
  examples: string[];
}

interface AgentCapabilities {
  streaming: boolean;
  pushNotifications: boolean;
  stateTransitionHistory: boolean;
}

interface AgentCard {
  name: string;
  description: string;
  url: string;
  version: string;
  protocolVersion: string;
  capabilities: AgentCapabilities;
  skills: AgentSkill[];
  defaultInputModes: string[];
  defaultOutputModes: string[];
}

interface Message {
  role: string;
  parts: { type: string; text?: string }[];
  messageId: string;
  taskId?: string;
  contextId?: string;
}

interface TaskStatus {
  state: string;
  message?: Message;
  timestamp: string;
}

interface Task {
  id: string;
  contextId: string;
  status: TaskStatus;
  history: Message[];
  metadata: Record<string, any>;
}

interface RPCLog {
  id: number;
  time: string;
  method: string;
  request: any;
  response?: any;
  error?: string;
  duration: number;
}

// ── Helpers ─────────────────────────────────────────────────

function formatTime(ts: string) {
  if (!ts) return '-';
  try {
    return new Date(ts).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  } catch { return ts; }
}

function shortId(id: string) {
  return id ? id.slice(0, 8) : '-';
}

const STATE_CONFIG: Record<string, { label: string; color: string; icon: typeof CheckCircle }> = {
  submitted: { label: '已提交', color: 'text-blue-500 bg-blue-500/10', icon: Clock },
  working: { label: '处理中', color: 'text-amber-500 bg-amber-500/10', icon: Loader2 },
  'input-required': { label: '需要输入', color: 'text-purple-500 bg-purple-500/10', icon: MessageSquare },
  completed: { label: '已完成', color: 'text-green-500 bg-green-500/10', icon: CheckCircle },
  failed: { label: '失败', color: 'text-red-500 bg-red-500/10', icon: XCircle },
  canceled: { label: '已取消', color: 'text-muted-foreground bg-muted', icon: Ban },
};

function StateBadge({ state }: { state: string }) {
  const cfg = STATE_CONFIG[state] || { label: state, color: 'text-muted-foreground bg-muted', icon: Clock };
  const Icon = cfg.icon;
  return (
    <Badge className={`gap-1 ${cfg.color}`}>
      <Icon className={`w-3 h-3 ${state === 'working' ? 'animate-spin' : ''}`} />
      {cfg.label}
    </Badge>
  );
}

// ── Agent Card View ────────────────────────────────────────

function AgentCardView({ card }: { card: AgentCard }) {
  const [expanded, setExpanded] = useState(true);

  return (
    <div className="rounded-lg border border-border bg-card">
      <Button
        onClick={() => setExpanded(!expanded)}
        variant="row" className="gap-2 p-4 hover:bg-muted/30 transition-colors"
      >
        <Globe className="w-4 h-4 text-primary" />
        <span className="text-sm font-medium flex-1">Agent Card</span>
        <Badge variant="default">
          v{card.protocolVersion}
        </Badge>
        {expanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
      </Button>
      {expanded && (
        <div className="px-4 pb-4 space-y-3 border-t border-border pt-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <div className="text-[10px] text-muted-foreground mb-1">名称</div>
              <div className="text-sm font-medium">{card.name}</div>
            </div>
            <div>
              <div className="text-[10px] text-muted-foreground mb-1">端点</div>
              <div className="text-xs font-mono text-muted-foreground">{card.url}</div>
            </div>
            <div className="sm:col-span-2">
              <div className="text-[10px] text-muted-foreground mb-1">描述</div>
              <div className="text-xs">{card.description}</div>
            </div>
          </div>

          {/* Capabilities */}
          <div>
            <div className="text-[10px] text-muted-foreground mb-1.5">能力</div>
            <div className="flex flex-wrap gap-1.5">
              {[
                { key: 'streaming', label: '流式传输', value: card.capabilities.streaming },
                { key: 'push', label: '推送通知', value: card.capabilities.pushNotifications },
                { key: 'history', label: '状态历史', value: card.capabilities.stateTransitionHistory },
              ].map((cap) => (
                <Badge key={cap.key} className={`gap-1 ${
                    cap.value
                      ? 'bg-emerald-500/10 text-emerald-500'
                      : 'bg-muted text-muted-foreground'
                  }`}>
                  {cap.value ? <CheckCircle className="w-2.5 h-2.5" /> : <XCircle className="w-2.5 h-2.5" />}
                  {cap.label}
                </Badge>
              ))}
            </div>
          </div>

          {/* Skills */}
          <div>
            <div className="text-[10px] text-muted-foreground mb-1.5">技能 ({card.skills.length})</div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {card.skills.map((skill) => (
                <div key={skill.id} className="rounded border border-border p-2.5 hover:bg-muted/30 transition-colors">
                  <div className="flex items-center gap-1.5 mb-1">
                    <Zap className="w-3 h-3 text-amber-500" />
                    <span className="text-xs font-medium">{skill.name}</span>
                    <span className="text-[10px] font-mono text-muted-foreground">({skill.id})</span>
                  </div>
                  <div className="text-[10px] text-muted-foreground mb-1.5">{skill.description}</div>
                  <div className="flex flex-wrap gap-1">
                    {skill.tags.map((tag) => (
                      <Badge key={tag} variant="muted">
                        {tag}
                      </Badge>
                    ))}
                  </div>
                  {skill.examples.length > 0 && (
                    <div className="mt-1.5 text-[10px] text-muted-foreground">
                      示例: {skill.examples[0]}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* IO Modes */}
          <div className="flex items-center gap-4 text-[10px] text-muted-foreground">
            <span>输入: {card.defaultInputModes.join(', ')}</span>
            <span>输出: {card.defaultOutputModes.join(', ')}</span>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Task Detail ────────────────────────────────────────────

function TaskDetail({ task, onCancel }: { task: Task; onCancel: (id: string) => void }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border border-border rounded-lg bg-card">
      <Button
        onClick={() => setExpanded(!expanded)}
        variant="row" className="gap-3 p-3 hover:bg-muted/30 transition-colors"
      >
        <div className="w-8 h-8 rounded-full flex items-center justify-center bg-primary/10">
          <FileText className="w-4 h-4 text-primary" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-mono">{shortId(task.id)}</span>
            <StateBadge state={task.status.state} />
          </div>
          <div className="text-[10px] text-muted-foreground mt-0.5">
            {task.history.length} 条消息 · {formatTime(task.status.timestamp)}
          </div>
        </div>
        {task.status.state === 'working' && (
          <Button
            onClick={(e) => { e.stopPropagation(); onCancel(task.id); }}
            variant="chip-xs" className="bg-red-500/10 text-red-500 hover:bg-red-500/20"
          >
            取消
          </Button>
        )}
        {expanded ? <ChevronDown className="w-3.5 h-3.5 shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 shrink-0" />}
      </Button>
      {expanded && (
        <div className="px-3 pb-3 border-t border-border pt-2 space-y-2">
          {/* Task Info */}
          <div className="grid grid-cols-2 gap-2 text-[10px]">
            <div>
              <span className="text-muted-foreground">Task ID: </span>
              <span className="font-mono">{task.id}</span>
            </div>
            <div>
              <span className="text-muted-foreground">Context ID: </span>
              <span className="font-mono">{shortId(task.contextId)}</span>
            </div>
          </div>

          {/* Message History */}
          <div className="space-y-1.5 max-h-[300px] overflow-auto">
            {task.history.map((msg, i) => {
              const isUser = msg.role === 'user';
              const text = msg.parts.filter(p => p.type === 'text').map(p => p.text).join('\n');
              return (
                <div key={i} className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
                  <div className={`max-w-[80%] rounded-lg px-2.5 py-1.5 text-xs ${
                    isUser
                      ? 'bg-primary/10 text-foreground'
                      : 'bg-muted text-muted-foreground'
                  }`}>
                    <div className="text-[10px] font-medium mb-0.5 opacity-60">
                      {isUser ? '用户' : 'Agent'}
                    </div>
                    <div className="whitespace-pre-wrap break-words">{text}</div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Status Message */}
          {task.status.message && (
            <div className="rounded border border-border bg-muted/30 p-2 text-[10px]">
              <span className="text-muted-foreground">最终状态消息: </span>
              {task.status.message.parts.filter(p => p.type === 'text').map(p => p.text).join('\n')}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── JSON-RPC Tester ────────────────────────────────────────

function RPCPanel({ onResult }: { onResult: (log: RPCLog) => void }) {
  const [method, setMethod] = useState('tasks/send');
  const [paramsText, setParamsText] = useState('{\n  "message": {\n    "role": "user",\n    "parts": [{"type": "text", "text": "你好"}]\n  }\n}');
  const [sending, setSending] = useState(false);

  const methods = [
    { value: 'tasks/send', label: 'tasks/send', desc: '创建/继续任务' },
    { value: 'tasks/get', label: 'tasks/get', desc: '查询任务状态' },
    { value: 'tasks/cancel', label: 'tasks/cancel', desc: '取消任务' },
  ];

  const handleSend = async () => {
    try {
      setSending(true);
      const params = JSON.parse(paramsText);
      const rpcId = Date.now();
      const body = {
        jsonrpc: '2.0',
        id: rpcId,
        method,
        params,
      };

      const start = Date.now();
      const res = await fetch('/a2a', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      const duration = Date.now() - start;

      onResult({
        id: rpcId,
        time: new Date().toISOString(),
        method,
        request: body,
        response: data,
        error: data.error ? JSON.stringify(data.error) : undefined,
        duration,
      });
    } catch (e: any) {
      onResult({
        id: Date.now(),
        time: new Date().toISOString(),
        method,
        request: null,
        error: e.message,
        duration: 0,
      });
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center gap-2 mb-3">
        <Code className="w-4 h-4 text-primary" />
        <span className="text-sm font-medium">JSON-RPC 测试器</span>
      </div>
      <div className="space-y-3">
        <div>
          <label className="text-xs text-muted-foreground">方法</label>
          <div className="flex gap-1.5 mt-1">
            {methods.map((m) => (
              <Button variant="bare"
                key={m.value}
                onClick={() => {
                  setMethod(m.value);
                  if (m.value === 'tasks/get') {
                    setParamsText('{\n  "id": "task-id-here"\n}');
                  } else if (m.value === 'tasks/cancel') {
                    setParamsText('{\n  "id": "task-id-here"\n}');
                  } else {
                    setParamsText('{\n  "message": {\n    "role": "user",\n    "parts": [{"type": "text", "text": "你好"}]\n  }\n}');
                  }
                }}
                className={`px-2.5 py-1.5 text-[10px] rounded-lg border transition-colors ${
                  method === m.value
                    ? 'bg-primary/10 border-primary/30 text-primary'
                    : 'border-border text-muted-foreground hover:bg-muted/50'
                }`}
              >
                {m.label}
              </Button>
            ))}
          </div>
          <div className="text-[10px] text-muted-foreground mt-1">
            {methods.find(m => m.value === method)?.desc}
          </div>
        </div>
        <div>
          <label className="text-xs text-muted-foreground">参数 (JSON)</label>
          <textarea
            className="w-full mt-1 px-3 py-2 text-xs font-mono bg-muted border border-border rounded-md h-32 resize-none focus:outline-none focus:ring-1 focus:ring-primary/50"
            value={paramsText}
            onChange={(e) => setParamsText(e.target.value)}
            spellCheck={false}
          />
        </div>
        <Button onClick={handleSend}
          disabled={sending} variant="primary" className="gap-1.5 px-4 py-1.5">
          {sending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Send className="w-3 h-3" />}
          {sending ? '发送中...' : '发送请求'}
        </Button>
      </div>
    </div>
  );
}

function RPCLogPanel({ logs }: { logs: RPCLog[] }) {
  const [expandedId, setExpandedId] = useState<number | null>(null);

  if (logs.length === 0) return null;

  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="flex items-center gap-2 p-4 border-b border-border">
        <Activity className="w-4 h-4" />
        <span className="text-sm font-medium">RPC 日志</span>
        <Badge variant="muted">{logs.length} 条</Badge>
      </div>
      <div className="divide-y divide-border max-h-[400px] overflow-auto">
        {logs.slice().reverse().map((log) => (
          <div key={log.id}>
            <Button
              onClick={() => setExpandedId(expandedId === log.id ? null : log.id)}
              variant="row" className="gap-2 px-4 py-2 hover:bg-muted/30 transition-colors"
            >
              {log.error ? (
                <XCircle className="w-3.5 h-3.5 text-red-500 shrink-0" />
              ) : (
                <CheckCircle className="w-3.5 h-3.5 text-green-500 shrink-0" />
              )}
              <span className="text-xs font-mono">{log.method}</span>
              <span className="text-[10px] text-muted-foreground">{log.duration}ms</span>
              <span className="text-[10px] text-muted-foreground ml-auto">{formatTime(log.time)}</span>
              {expandedId === log.id ? <ChevronDown className="w-3 h-3 shrink-0" /> : <ChevronRight className="w-3 h-3 shrink-0" />}
            </Button>
            {expandedId === log.id && (
              <div className="px-4 pb-3 space-y-2">
                {log.request && (
                  <div>
                    <div className="text-[10px] text-muted-foreground mb-0.5">请求</div>
                    <pre className="text-[10px] font-mono bg-muted rounded p-2 overflow-auto max-h-[150px]">
                      {JSON.stringify(log.request, null, 2)}
                    </pre>
                  </div>
                )}
                {log.response && (
                  <div>
                    <div className="text-[10px] text-muted-foreground mb-0.5">响应</div>
                    <pre className="text-[10px] font-mono bg-muted rounded p-2 overflow-auto max-h-[200px]">
                      {JSON.stringify(log.response, null, 2)}
                    </pre>
                  </div>
                )}
                {log.error && (
                  <div className="text-xs text-red-500 bg-red-500/5 rounded p-2">{log.error}</div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────

export default function A2APage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [card, setCard] = useState<AgentCard | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [rpcLogs, setRpcLogs] = useState<RPCLog[]>([]);
  const [tab, setTab] = useState<'card' | 'tasks' | 'tester'>('card');
  const [searchQuery, setSearchQuery] = useState('');
  const [stateFilter, setStateFilter] = useState('all');

  const fetchCard = useCallback(async () => {
    try {
      const res = await fetch('/.well-known/agent.json');
      if (res.ok) {
        const data = await res.json();
        setCard(data);
      }
    } catch (e: any) {
      setError('无法获取 Agent Card: ' + e.message);
    }
  }, []);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError('');
    await fetchCard();
    setLoading(false);
  }, [fetchCard]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleCancelTask = async (taskId: string) => {
    try {
      const body = {
        jsonrpc: '2.0',
        id: Date.now(),
        method: 'tasks/cancel',
        params: { id: taskId },
      };
      const res = await fetch('/a2a', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (data.result) {
        setTasks((prev) =>
          prev.map((t) => t.id === taskId ? { ...t, status: { ...t.status, state: 'canceled' } } : t)
        );
      }
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleRPCResult = (log: RPCLog) => {
    setRpcLogs((prev) => [...prev, log]);

    // If task result, update tasks list
    if (log.response?.result && !log.error) {
      const result = log.response.result;
      if (result.id && result.status) {
        setTasks((prev) => {
          const existing = prev.findIndex((t) => t.id === result.id);
          if (existing >= 0) {
            const updated = [...prev];
            updated[existing] = result;
            return updated;
          }
          return [result, ...prev];
        });
      }
    }
  };

  const filteredTasks = tasks.filter((t) => {
    if (stateFilter !== 'all' && t.status.state !== stateFilter) return false;
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      return t.id.toLowerCase().includes(q) ||
        t.history.some(m => m.parts.some(p => p.text?.toLowerCase().includes(q)));
    }
    return true;
  });

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 p-2 text-xs text-red-500 bg-red-500/5 rounded-md border border-red-500/20">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <Button variant="bare" onClick={() => setError('')}><X className="w-3 h-3" /></Button>
        </div>
      )}

      {/* Stats Row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1">
            <Globe className="w-3.5 h-3.5" />协议版本
          </div>
          <div className="text-sm font-medium">A2A v{card?.protocolVersion || '-'}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1">
            <Zap className="w-3.5 h-3.5" />技能数量
          </div>
          <div className="text-sm font-medium">{card?.skills.length || 0}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1">
            <FileText className="w-3.5 h-3.5" />任务数量
          </div>
          <div className="text-sm font-medium">{tasks.length}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1">
            <Activity className="w-3.5 h-3.5" />RPC 调用
          </div>
          <div className="text-sm font-medium">{rpcLogs.length}</div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-border">
        {([
          { key: 'card', label: 'Agent Card', icon: Globe },
          { key: 'tasks', label: '任务管理', icon: FileText },
          { key: 'tester', label: 'RPC 测试', icon: Code },
        ] as { key: typeof tab; label: string; icon: any }[]).map((t) => (
          <Button
            key={t.key}
            onClick={() => setTab(t.key)}
            variant="tabline" className={`font-medium  ${
              tab === t.key
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            <t.icon className="w-3.5 h-3.5" />
            {t.label}
          </Button>
        ))}
        <div className="flex-1" />
        <Button onClick={fetchData} variant="secondary" className="mb-1">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
      </div>

      {/* Card Tab */}
      {tab === 'card' && card && <AgentCardView card={card} />}

      {/* Tasks Tab */}
      {tab === 'tasks' && (
        <div className="space-y-3">
          {/* Search & Filter */}
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
              <input
                className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                placeholder="搜索任务..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
            </div>
            <div className="flex gap-1">
              {['all', 'working', 'completed', 'failed', 'canceled'].map((s) => (
                <Button
                  key={s}
                  onClick={() => setStateFilter(s)}
                  variant="chip-xs" className={`border transition-colors  ${
                    stateFilter === s
                      ? 'bg-primary/10 border-primary/30 text-primary'
                      : 'border-border text-muted-foreground hover:bg-muted/50'
                  }`}
                >
                  {s === 'all' ? '全部' : STATE_CONFIG[s]?.label || s}
                </Button>
              ))}
            </div>
          </div>

          {/* Task List */}
          {filteredTasks.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <FileText className="w-10 h-10 mb-2 opacity-30" />
              <p className="text-xs">暂无任务</p>
              <p className="text-[10px] mt-1">使用 RPC 测试器发送任务</p>
            </div>
          ) : (
            <div className="space-y-2">
              {filteredTasks.map((task) => (
                <TaskDetail key={task.id} task={task} onCancel={handleCancelTask} />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Tester Tab */}
      {tab === 'tester' && (
        <div className="space-y-4">
          <RPCPanel onResult={handleRPCResult} />
          <RPCLogPanel logs={rpcLogs} />
        </div>
      )}
    </div>
  );
}
