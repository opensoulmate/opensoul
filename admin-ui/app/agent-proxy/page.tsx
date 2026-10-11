'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Send, X, CheckCircle, XCircle, Clock, Bot, Terminal,
  Loader2, RefreshCw, ChevronDown, ChevronRight, Zap,
  AlertCircle, MessageSquare, Copy, Play, Server, Cpu,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

// ── Types ───────────────────────────────────────────────────

interface AgentInfo {
  id: string;
  name: string;
  description: string;
  available: boolean;
  binary: string;
}

interface ConversationEntry {
  id: number;
  time: string;
  agentId: string;
  agentName: string;
  message: string;
  response: string;
  success: boolean;
  error?: string;
  duration: number;
}

// ── Helpers ─────────────────────────────────────────────────

function formatTime(ts: string) {
  if (!ts) return '-';
  try {
    return new Date(ts).toLocaleString('zh-CN', {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  } catch { return ts; }
}

// ── Agent Card ──────────────────────────────────────────────

const AGENT_ICONS: Record<string, string> = {
  hermes: '🟣',
  mimo: '🔵',
  claude: '🟠',
  codex: '🟢',
  aider: '🟡',
};

function AgentCard({
  agent,
  selected,
  onSelect,
}: {
  agent: AgentInfo;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <Button
      onClick={onSelect}
      variant="row" className={`gap-3 p-3 rounded-lg border transition-all  ${
        selected
          ? 'border-primary bg-primary/5 shadow-sm'
          : agent.available
          ? 'border-border bg-card hover:border-primary/30 hover:bg-muted/30'
          : 'border-border bg-card opacity-50'
      }`}
    >
      <div className="text-2xl">{AGENT_ICONS[agent.id] || '⚪'}</div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{agent.name}</span>
          <Badge className={`gap-1 ${
            agent.available
              ? 'bg-emerald-500/10 text-emerald-500'
              : 'bg-muted text-muted-foreground'
          }`}>
            {agent.available ? <CheckCircle className="w-2.5 h-2.5" /> : <XCircle className="w-2.5 h-2.5" />}
            {agent.available ? '可用' : '未安装'}
          </Badge>
        </div>
        <div className="text-[10px] text-muted-foreground mt-0.5">{agent.description}</div>
        <div className="text-[10px] font-mono text-muted-foreground mt-0.5">
          <Terminal className="w-2.5 h-2.5 inline mr-0.5" />
          {agent.binary}
        </div>
      </div>
    </Button>
  );
}

// ── Chat Panel ──────────────────────────────────────────────

function ChatPanel({
  agent,
  onSend,
  conversations,
  sending,
}: {
  agent: AgentInfo;
  onSend: (msg: string) => void;
  conversations: ConversationEntry[];
  sending: boolean;
}) {
  const [input, setInput] = useState('');

  const agentConvs = conversations.filter((c) => c.agentId === agent.id);

  const handleSubmit = () => {
    if (!input.trim() || sending) return;
    onSend(input.trim());
    setInput('');
  };

  return (
    <div className="flex flex-col h-full rounded-lg border border-border bg-card">
      {/* Header */}
      <div className="flex items-center gap-2 p-3 border-b border-border">
        <span className="text-xl">{AGENT_ICONS[agent.id] || '⚪'}</span>
        <div className="flex-1">
          <div className="text-sm font-medium">{agent.name}</div>
          <div className="text-[10px] text-muted-foreground">{agent.description}</div>
        </div>
        <Badge className={`${
          agent.available ? 'bg-emerald-500/10 text-emerald-500' : 'bg-red-500/10 text-red-500'
        }`}>
          {agent.available ? '在线' : '离线'}
        </Badge>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-auto p-3 space-y-3 min-h-[300px] max-h-[500px]">
        {agentConvs.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
            <MessageSquare className="w-8 h-8 mb-2 opacity-30" />
            <p className="text-xs">发送消息开始对话</p>
          </div>
        ) : (
          agentConvs.map((conv) => (
            <div key={conv.id} className="space-y-2">
              {/* User message */}
              <div className="flex justify-end">
                <div className="max-w-[80%] bg-primary/10 rounded-lg px-3 py-2 text-xs">
                  {conv.message}
                </div>
              </div>
              {/* Agent response */}
              <div className="flex justify-start">
                <div className={`max-w-[80%] rounded-lg px-3 py-2 text-xs ${
                  conv.success ? 'bg-muted' : 'bg-red-500/5 border border-red-500/20'
                }`}>
                  {conv.success ? (
                    <div className="whitespace-pre-wrap break-words">{conv.response}</div>
                  ) : (
                    <div>
                      <div className="text-red-500 text-[10px] mb-1">
                        <XCircle className="w-3 h-3 inline mr-0.5" />
                        错误
                      </div>
                      <div className="text-red-400">{conv.error || conv.response}</div>
                    </div>
                  )}
                  <div className="text-[10px] text-muted-foreground mt-1 flex items-center gap-2">
                    <span>{formatTime(conv.time)}</span>
                    <span>{conv.duration}ms</span>
                  </div>
                </div>
              </div>
            </div>
          ))
        )}
        {sending && (
          <div className="flex justify-start">
            <div className="bg-muted rounded-lg px-3 py-2 text-xs flex items-center gap-2">
              <Loader2 className="w-3 h-3 animate-spin" />
              <span className="text-muted-foreground">{agent.name} 思考中...</span>
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="p-3 border-t border-border">
        <div className="flex gap-2">
          <input
            className="flex-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder={agent.available ? `发送消息给 ${agent.name}...` : 'Agent 不可用'}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleSubmit()}
            disabled={!agent.available || sending}
          />
          <Button onClick={handleSubmit}
            disabled={!input.trim() || !agent.available || sending} variant="primary">
            {sending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Send className="w-3 h-3" />}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────

export default function AgentProxyPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<AgentInfo | null>(null);
  const [conversations, setConversations] = useState<ConversationEntry[]>([]);
  const [sending, setSending] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [showHistory, setShowHistory] = useState(false);
  const [filterAvail, setFilterAvail] = useState<'all' | 'available' | 'unavailable'>('all');

  const fetchAgents = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiFetch('/api/agent-proxy/agents');
      setAgents(data);
      if (!selectedAgent && data.length > 0) {
        const firstAvail = data.find((a: AgentInfo) => a.available);
        setSelectedAgent(firstAvail || data[0]);
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAgents(); }, [fetchAgents]);

  const handleSend = async (message: string) => {
    if (!selectedAgent) return;
    setSending(true);
    const start = Date.now();

    try {
      const data = await apiFetch('/api/agent-proxy/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agent_id: selectedAgent.id,
          message,
        }),
      });

      const entry: ConversationEntry = {
        id: Date.now(),
        time: new Date().toISOString(),
        agentId: selectedAgent.id,
        agentName: selectedAgent.name,
        message,
        response: data.response || '',
        success: data.success,
        error: data.error,
        duration: Date.now() - start,
      };
      setConversations((prev) => [...prev, entry]);
    } catch (e: any) {
      const entry: ConversationEntry = {
        id: Date.now(),
        time: new Date().toISOString(),
        agentId: selectedAgent.id,
        agentName: selectedAgent.name,
        message,
        response: '',
        success: false,
        error: e.message,
        duration: Date.now() - start,
      };
      setConversations((prev) => [...prev, entry]);
    } finally {
      setSending(false);
    }
  };

  const filteredAgents = agents.filter((a) => {
    if (filterAvail === 'available' && !a.available) return false;
    if (filterAvail === 'unavailable' && a.available) return false;
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      return a.name.toLowerCase().includes(q) ||
        a.description.toLowerCase().includes(q) ||
        a.id.toLowerCase().includes(q);
    }
    return true;
  });

  const availableCount = agents.filter((a) => a.available).length;
  const totalMessages = conversations.length;

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

      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1">
            <Bot className="w-3.5 h-3.5" />Agent 总数
          </div>
          <div className="text-sm font-medium">{agents.length}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1">
            <CheckCircle className="w-3.5 h-3.5" />可用
          </div>
          <div className="text-sm font-medium text-emerald-600">{availableCount}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1">
            <MessageSquare className="w-3.5 h-3.5" />对话数
          </div>
          <div className="text-sm font-medium">{totalMessages}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1">
            <Zap className="w-3.5 h-3.5" />成功率
          </div>
          <div className="text-sm font-medium">
            {totalMessages > 0
              ? `${Math.round((conversations.filter(c => c.success).length / totalMessages) * 100)}%`
              : '-'}
          </div>
        </div>
      </div>

      {/* Main Content: Agent List + Chat */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Agent List */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">Agent 列表</span>
            <Button
              onClick={fetchAgents}
              variant="icon-sm" className="hover:bg-muted"
              title="刷新"
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </Button>
          </div>
          <div className="relative">
            <input
              className="w-full pl-7 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
              placeholder="搜索 Agent..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            <Cpu className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          </div>
          <div className="flex gap-1">
            {[
              { value: 'all' as const, label: '全部' },
              { value: 'available' as const, label: '可用' },
              { value: 'unavailable' as const, label: '未安装' },
            ].map((f) => (
              <Button variant="bare"
                key={f.value}
                onClick={() => setFilterAvail(f.value)}
                className={`px-2 py-0.5 text-[10px] rounded-lg border transition-colors ${
                  filterAvail === f.value
                    ? 'bg-primary/10 border-primary/30 text-primary'
                    : 'border-border text-muted-foreground hover:bg-muted/50'
                }`}
              >
                {f.label}
              </Button>
            ))}
          </div>
          <div className="space-y-2">
            {filteredAgents.map((agent) => (
              <AgentCard
                key={agent.id}
                agent={agent}
                selected={selectedAgent?.id === agent.id}
                onSelect={() => setSelectedAgent(agent)}
              />
            ))}
            {filteredAgents.length === 0 && (
              <div className="text-xs text-muted-foreground text-center py-4">无匹配 Agent</div>
            )}
          </div>
        </div>

        {/* Chat Panel */}
        <div className="lg:col-span-2">
          {selectedAgent ? (
            <ChatPanel
              agent={selectedAgent}
              onSend={handleSend}
              conversations={conversations}
              sending={sending}
            />
          ) : (
            <div className="flex flex-col items-center justify-center h-64 text-muted-foreground rounded-lg border border-border bg-card">
              <Bot className="w-10 h-10 mb-2 opacity-30" />
              <p className="text-xs">选择一个 Agent 开始对话</p>
            </div>
          )}
        </div>
      </div>

      {/* Conversation History */}
      {conversations.length > 0 && (
        <div className="rounded-lg border border-border bg-card">
          <Button
            onClick={() => setShowHistory(!showHistory)}
            variant="row" className="gap-2 p-3 hover:bg-muted/30 transition-colors"
          >
            <Clock className="w-4 h-4" />
            <span className="text-sm font-medium">对话历史</span>
            <Badge variant="muted">
              {conversations.length} 条
            </Badge>
            <div className="flex-1" />
            {showHistory ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
          </Button>
          {showHistory && (
            <div className="border-t border-border divide-y divide-border max-h-[300px] overflow-auto">
              {conversations.slice().reverse().map((conv) => (
                <div key={conv.id} className="flex items-center gap-3 px-3 py-2 text-xs hover:bg-muted/20">
                  <span className="text-base">{AGENT_ICONS[conv.agentId] || '⚪'}</span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{conv.agentName}</span>
                      <span className="text-muted-foreground truncate">{conv.message}</span>
                    </div>
                    <div className="text-[10px] text-muted-foreground truncate">
                      {conv.success ? conv.response.slice(0, 100) : conv.error}
                    </div>
                  </div>
                  {conv.success ? (
                    <CheckCircle className="w-3 h-3 text-emerald-500 shrink-0" />
                  ) : (
                    <XCircle className="w-3 h-3 text-red-500 shrink-0" />
                  )}
                  <span className="text-[10px] text-muted-foreground shrink-0">{conv.duration}ms</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
