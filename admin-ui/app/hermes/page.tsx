'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, RefreshCw, X, MessageSquare, Send, Trash2,
  Bot, Globe, Smartphone, Monitor, Clock, AlertTriangle,
  Loader2, Eye, ArrowRight, Filter,
} from 'lucide-react';

// ── Types ───────────────────────────────────────────────────

interface HermesSession {
  id: string;
  name: string;
  platform: string;
  chat_id: string;
  workspace: string;
  last_active: string;
  last_message: string;
}

interface Message {
  role: string;
  content: string;
  timestamp: string;
  source: string;
}

// ── Helpers ─────────────────────────────────────────────────

const PLATFORM_CONFIG: Record<string, { label: string; icon: typeof Globe; color: string }> = {
  hermes: { label: 'CLI', icon: Monitor, color: 'text-blue-500 bg-blue-500/10' },
  wechat: { label: '微信', icon: Smartphone, color: 'text-green-500 bg-green-500/10' },
  telegram: { label: 'Telegram', icon: Globe, color: 'text-sky-500 bg-sky-500/10' },
  discord: { label: 'Discord', icon: Globe, color: 'text-indigo-500 bg-indigo-500/10' },
};

function getPlatformConfig(platform: string) {
  return PLATFORM_CONFIG[platform] || { label: platform, icon: Globe, color: 'text-muted-foreground bg-muted' };
}

function formatRelative(ts: string) {
  if (!ts) return '';
  try {
    const diff = Date.now() - new Date(ts).getTime();
    if (diff < 60000) return '刚刚';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`;
    return `${Math.floor(diff / 86400000)}天前`;
  } catch { return ts; }
}

// ── Session Card ────────────────────────────────────────────

function SessionCard({ session, onSend, onDelete }: {
  session: HermesSession;
  onSend: () => void;
  onDelete: () => void;
}) {
  const pc = getPlatformConfig(session.platform);
  const Icon = pc.icon;

  return (
    <div className="flex items-center gap-3 p-3 rounded-lg border border-border bg-card hover:bg-muted/30 transition-colors">
      <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${pc.color}`}>
        <Icon className="w-5 h-5" />
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium truncate">{session.name}</span>
          <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${pc.color}`}>{pc.label}</span>
        </div>
        {session.last_message && (
          <p className="text-[10px] text-muted-foreground truncate mt-0.5">{session.last_message}</p>
        )}
        <div className="flex items-center gap-3 mt-1 text-[10px] text-muted-foreground">
          {session.workspace && <span>📂 {session.workspace}</span>}
          {session.last_active && <span className="flex items-center gap-1"><Clock className="w-2.5 h-2.5" />{session.last_active}</span>}
        </div>
      </div>

      <div className="flex items-center gap-1 shrink-0">
        <button onClick={onSend} className="p-1.5 rounded-md hover:bg-muted" title="发送消息">
          <Send className="w-3.5 h-3.5 text-muted-foreground" />
        </button>
        <button onClick={onDelete} className="p-1.5 rounded-md hover:bg-red-500/10" title="删除会话">
          <Trash2 className="w-3.5 h-3.5 text-muted-foreground hover:text-red-500" />
        </button>
      </div>
    </div>
  );
}

// ── Send Dialog ─────────────────────────────────────────────

function SendDialog({ sessionId, onClose }: { sessionId: string; onClose: () => void }) {
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState('');

  const handleSend = async () => {
    if (!text.trim()) return;
    setSending(true);
    try {
      const data = await apiFetch('/api/hermes/sessions/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, session_id: sessionId }),
      });
      setResult(data.status === 'ok' ? '消息已发送' : `状态: ${data.status}`);
      setText('');
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
          <h2 className="text-sm font-medium">发送消息到 {sessionId.slice(0, 20)}</h2>
          <button onClick={onClose} className="p-1 hover:bg-muted rounded-md"><X className="w-4 h-4" /></button>
        </div>
        <div className="p-4 space-y-3">
          <textarea
            className="w-full px-3 py-2 text-xs bg-muted border border-border rounded-md h-24 resize-none focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="输入消息内容..."
            value={text}
            onChange={e => setText(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleSend(); }}
          />
          {result && <div className="text-xs p-2 rounded bg-muted">{result}</div>}
          <div className="flex justify-end gap-2">
            <button onClick={onClose} className="px-3 py-1.5 text-xs lg:text-sm border border-border rounded-lg hover:bg-muted">关闭</button>
            <button onClick={handleSend} disabled={!text.trim() || sending}
              className="flex items-center gap-2 px-2 lg:px-2 lg:px-4 py-2 text-xs lg:text-sm bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 disabled:opacity-50 font-medium transition-colors">
              {sending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Send className="w-3 h-3" />}发送
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────

export default function HermesBridgePage() {
  const [sessions, setSessions] = useState<HermesSession[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [platformFilter, setPlatformFilter] = useState('all');
  const [sendTarget, setSendTarget] = useState<string | null>(null);
  const [limit, setLimit] = useState(50);

  const fetchSessions = useCallback(async () => {
    try {
      setLoading(true);
      setError('');
      const params = new URLSearchParams({ limit: String(limit) });
      if (platformFilter !== 'all') params.set('source', platformFilter);
      const data = await apiFetch(`/api/hermes/sessions?${params}`);
      setSessions(Array.isArray(data.sessions) ? data.sessions : []);
      setTotal(data.total || 0);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [limit, platformFilter]);

  useEffect(() => { fetchSessions(); }, [fetchSessions]);

  const handleDelete = async (id: string) => {
    if (!confirm(`确定删除会话 ${id}？`)) return;
    try {
      await apiFetch(`/api/hermes/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' });
      fetchSessions();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const platforms = Array.from(new Set(sessions.map(s => s.platform))).sort();

  const filtered = sessions.filter(s => {
    if (search) {
      const q = search.toLowerCase();
      return s.name.toLowerCase().includes(q) || s.id.toLowerCase().includes(q) ||
        s.platform.toLowerCase().includes(q) || (s.last_message || '').toLowerCase().includes(q);
    }
    return true;
  });

  const platformCounts = sessions.reduce((acc, s) => {
    acc[s.platform] = (acc[s.platform] || 0) + 1;
    return acc;
  }, {} as Record<string, number>);

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{total}</div>
          <div className="text-xs text-muted-foreground">总会话</div>
        </div>
        {Object.entries(platformCounts).slice(0, 3).map(([platform, count]) => {
          const pc = getPlatformConfig(platform);
          return (
            <div key={platform} className="rounded-lg border border-border bg-card p-3">
              <div className="flex items-center gap-1.5 mb-1">
                <pc.icon className={`w-3.5 h-3.5 ${pc.color.split(' ')[0]}`} />
                <span className="text-xs text-muted-foreground">{pc.label}</span>
              </div>
              <div className="text-2xl font-bold">{count}</div>
            </div>
          );
        })}
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索会话名称、ID、平台..." value={search} onChange={e => setSearch(e.target.value)} />
        </div>
        <select className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={platformFilter} onChange={e => setPlatformFilter(e.target.value)}>
          <option value="all">全部平台</option>
          <option value="cli">CLI</option>
          <option value="wx">微信</option>
          <option value="tg">Telegram</option>
          <option value="dc">Discord</option>
        </select>
        <button onClick={fetchSessions} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm border border-border rounded-lg hover:bg-muted transition-colors">
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

      {/* Session list */}
      <div className="space-y-1.5">
        {filtered.map(session => (
          <SessionCard
            key={session.id}
            session={session}
            onSend={() => setSendTarget(session.id)}
            onDelete={() => handleDelete(session.id)}
          />
        ))}
      </div>

      {filtered.length === 0 && (
        <div className="text-center py-12 text-muted-foreground">
          <MessageSquare className="w-10 h-10 mx-auto mb-2 opacity-30" />
          <p className="text-xs">{search ? '无匹配会话' : '暂无Hermes会话'}</p>
          <p className="text-[10px] mt-1">通过Hermes CLI或各平台消息会自动同步</p>
        </div>
      )}

      {/* Info */}
      <div className="rounded-lg border border-blue-500/30 bg-blue-500/5 p-3 flex items-center gap-2">
        <Globe className="w-4 h-4 text-blue-500 shrink-0" />
        <div>
          <span className="text-xs font-medium text-blue-500">Hermes Bridge</span>
          <p className="text-[10px] text-muted-foreground mt-0.5">
            跨平台会话桥接 — 统一管理 CLI、微信、Telegram、Discord 的消息会话。
            支持消息发送、会话搜索、历史查看。
          </p>
        </div>
      </div>

      {/* Send dialog */}
      {sendTarget && <SendDialog sessionId={sendTarget} onClose={() => setSendTarget(null)} />}
    </div>
  );
}
