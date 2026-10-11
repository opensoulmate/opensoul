'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Send, X, RefreshCw, Loader2, MessageSquare, BookOpen,
  AlertCircle, Search, Settings, ChevronDown, ChevronRight,
  Copy, CheckCircle, Zap, Database, Brain, BarChart3,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

// ── Types ───────────────────────────────────────────────────

interface Source {
  id: string;
  score: number;
}

interface ChatMessage {
  id: number;
  role: 'user' | 'assistant' | 'system';
  content: string;
  sources?: Source[];
  time: string;
  duration?: number;
}

interface ChatConfig {
  topK: number;
  stream: boolean;
}

// ── Helpers ─────────────────────────────────────────────────

function formatTime(ts: string) {
  if (!ts) return '-';
  try {
    return new Date(ts).toLocaleTimeString('zh-CN', {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  } catch { return ts; }
}

function SourceBadge({ source, index }: { source: Source; index: number }) {
  const scoreColor = source.score >= 0.8 ? 'text-green-500' :
    source.score >= 0.5 ? 'text-amber-500' : 'text-red-500';

  return (
    <Badge variant="muted" className="gap-1 font-mono">
      <span className="text-muted-foreground">#{index + 1}</span>
      <span className={scoreColor}>{source.score.toFixed(3)}</span>
    </Badge>
  );
}

// ── Message Bubble ──────────────────────────────────────────

function MessageBubble({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === 'user';
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(msg.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div className={`max-w-[85%] ${isUser ? '' : 'space-y-1.5'}`}>
        {/* Sources */}
        {msg.sources && msg.sources.length > 0 && (
          <div className="flex flex-wrap gap-1 mb-1">
            <span className="text-[10px] text-muted-foreground mr-1">
              <Database className="w-2.5 h-2.5 inline mr-0.5" />
              来源:
            </span>
            {msg.sources.map((s, i) => (
              <SourceBadge key={s.id || i} source={s} index={i} />
            ))}
          </div>
        )}

        {/* Message Content */}
        <div className={`relative group rounded-lg px-3 py-2 text-xs ${
          isUser
            ? 'bg-primary text-primary-foreground'
            : 'bg-muted text-foreground'
        }`}>
          <div className="whitespace-pre-wrap break-words">{msg.content}</div>
          <button
            onClick={handleCopy}
            className="absolute top-1 right-1 opacity-0 group-hover:opacity-100 transition-opacity p-0.5 rounded-md hover:bg-black/10"
            title="复制"
          >
            {copied ? (
              <CheckCircle className="w-3 h-3 text-green-500" />
            ) : (
              <Copy className="w-3 h-3 text-muted-foreground" />
            )}
          </button>
        </div>

        {/* Meta */}
        <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
          <span>{formatTime(msg.time)}</span>
          {msg.duration !== undefined && (
            <span>{msg.duration}ms</span>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Config Panel ────────────────────────────────────────────

function ConfigPanel({
  config,
  onChange,
}: {
  config: ChatConfig;
  onChange: (c: ChatConfig) => void;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-lg border border-border bg-card">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full px-3 py-2 text-left hover:bg-muted/30 transition-colors"
      >
        <Settings className="w-3.5 h-3.5" />
        <span className="text-xs font-medium">RAG 配置</span>
        <span className="text-[10px] text-muted-foreground ml-auto">
          top_k={config.topK} · {config.stream ? '流式' : '非流式'}
        </span>
        {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
      </button>
      {expanded && (
        <div className="px-3 pb-3 border-t border-border pt-2 space-y-2">
          <div>
            <label className="text-xs text-muted-foreground">Top K (检索条数)</label>
            <input
              type="range"
              min={1}
              max={20}
              value={config.topK}
              onChange={(e) => onChange({ ...config, topK: parseInt(e.target.value) })}
              className="w-full mt-1"
            />
            <div className="text-[10px] text-muted-foreground text-right">{config.topK}</div>
          </div>
          <div className="flex items-center justify-between">
            <label className="text-xs text-muted-foreground">SSE 流式响应</label>
            <button
              onClick={() => onChange({ ...config, stream: !config.stream })}
              className={`w-8 h-4 rounded-full transition-colors relative ${
                config.stream ? 'bg-primary' : 'bg-muted'
              }`}
            >
              <div className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform ${
                config.stream ? 'translate-x-4' : 'translate-x-0.5'
              }`} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────

export default function ChatTestPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const [config, setConfig] = useState<ChatConfig>({ topK: 5, stream: true });
  const [stats, setStats] = useState({ totalMessages: 0, totalSources: 0, avgResponseTime: 0 });
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => { scrollToBottom(); }, [messages]);

  useEffect(() => {
    const total = messages.filter(m => m.role === 'assistant').length;
    const sources = messages.reduce((s, m) => s + (m.sources?.length || 0), 0);
    const durations = messages.filter(m => m.duration !== undefined).map(m => m.duration!);
    const avg = durations.length > 0 ? durations.reduce((a, b) => a + b, 0) / durations.length : 0;
    setStats({ totalMessages: total, totalSources: sources, avgResponseTime: Math.round(avg) });
  }, [messages]);

  const handleSend = async () => {
    if (!input.trim() || sending) return;

    const userMsg: ChatMessage = {
      id: Date.now(),
      role: 'user',
      content: input.trim(),
      time: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setSending(true);
    setError('');

    const start = Date.now();

    try {
      if (config.stream) {
        // SSE streaming
        const res = await fetch('/api/chat/', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${localStorage.getItem('soul_token') || ''}`,
          },
          body: JSON.stringify({
            question: userMsg.content,
            top_k: config.topK,
            stream: true,
          }),
        });

        if (!res.ok) throw new Error(`API error ${res.status}`);

        const reader = res.body?.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let content = '';
        let sources: Source[] = [];
        const assistantId = Date.now() + 1;

        // Add empty assistant message
        setMessages((prev) => [...prev, {
          id: assistantId,
          role: 'assistant',
          content: '',
          time: new Date().toISOString(),
        }]);

        if (reader) {
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
              if (!line.startsWith('data: ')) continue;
              const data = line.slice(6).trim();
              if (data === '[DONE]') continue;

              try {
                const parsed = JSON.parse(data);
                if (parsed.type === 'sources') {
                  sources = parsed.content || [];
                  setMessages((prev) =>
                    prev.map(m => m.id === assistantId ? { ...m, sources } : m)
                  );
                } else if (parsed.type === 'content') {
                  content += parsed.content || '';
                  setMessages((prev) =>
                    prev.map(m => m.id === assistantId ? { ...m, content } : m)
                  );
                } else if (parsed.type === 'error') {
                  setError(parsed.content);
                }
              } catch { /* ignore parse errors */ }
            }
          }

          // Update final duration
          setMessages((prev) =>
            prev.map(m => m.id === assistantId ? { ...m, duration: Date.now() - start } : m)
          );
        }
      } else {
        // Non-streaming
        const data = await apiFetch('/api/chat/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            question: userMsg.content,
            top_k: config.topK,
            stream: false,
          }),
        });

        const assistantMsg: ChatMessage = {
          id: Date.now() + 1,
          role: 'assistant',
          content: data.answer || '无回答',
          sources: data.sources || [],
          time: new Date().toISOString(),
          duration: Date.now() - start,
        };
        setMessages((prev) => [...prev, assistantMsg]);
      }
    } catch (e: any) {
      setError(e.message);
      // Add error message
      setMessages((prev) => [...prev, {
        id: Date.now() + 1,
        role: 'assistant',
        content: `错误: ${e.message}`,
        time: new Date().toISOString(),
        duration: Date.now() - start,
      }]);
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  };

  const handleClear = () => {
    setMessages([]);
    setError('');
  };

  return (
    <div className="flex flex-col h-[calc(100vh-120px)]">
      {/* Stats Bar */}
      <div className="flex items-center gap-4 mb-3">
        <div className="flex items-center gap-4 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <MessageSquare className="w-3.5 h-3.5" />
            {stats.totalMessages} 回答
          </span>
          <span className="flex items-center gap-1">
            <Database className="w-3.5 h-3.5" />
            {stats.totalSources} 来源
          </span>
          {stats.avgResponseTime > 0 && (
            <span className="flex items-center gap-1">
              <Zap className="w-3.5 h-3.5" />
              平均 {stats.avgResponseTime}ms
            </span>
          )}
        </div>
        <div className="flex-1" />
        <ConfigPanel config={config} onChange={setConfig} />
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 p-2 mb-2 text-xs text-red-500 bg-red-500/5 rounded-md border border-red-500/20">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <button onClick={() => setError('')}><X className="w-3 h-3" /></button>
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-auto rounded-lg border border-border bg-card p-4 space-y-4">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
            <Brain className="w-12 h-12 mb-3 opacity-20" />
            <p className="text-sm font-medium">RAG 知识问答</p>
            <p className="text-xs mt-1">基于知识库的智能检索增强生成</p>
            <div className="grid grid-cols-2 gap-2 mt-4 max-w-md">
              {[
                '搜索知识库中的技术文档',
                '总结最近的知识条目',
                '查找相关的决策记录',
                '分析知识库中的模式',
              ].map((q) => (
                <button
                  key={q}
                  onClick={() => { setInput(q); inputRef.current?.focus(); }}
                  className="text-[10px] text-left px-3 py-2 rounded-lg border border-border hover:bg-muted/50 transition-colors"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <>
            {messages.map((msg) => (
              <MessageBubble key={msg.id} msg={msg} />
            ))}
            {sending && messages[messages.length - 1]?.role === 'user' && (
              <div className="flex justify-start">
                <div className="bg-muted rounded-lg px-3 py-2 text-xs flex items-center gap-2">
                  <Loader2 className="w-3 h-3 animate-spin" />
                  <span className="text-muted-foreground">检索知识库并生成回答...</span>
                </div>
              </div>
            )}
          </>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="mt-3 flex items-center gap-2">
        <Button onClick={handleClear}
          
          title="清空对话" variant="secondary" className="p-2">
          <RefreshCw className="w-4 h-4 text-muted-foreground" />
        </Button>
        <div className="flex-1 relative">
          <input
            ref={inputRef}
            className="w-full px-4 py-2 text-xs bg-muted border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-primary/50 pr-10"
            placeholder="输入问题，基于知识库检索回答..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleSend()}
            disabled={sending}
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || sending}
            className="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded-md hover:bg-primary/10 disabled:opacity-30"
          >
            {sending ? (
              <Loader2 className="w-4 h-4 text-primary animate-spin" />
            ) : (
              <Send className="w-4 h-4 text-primary" />
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
