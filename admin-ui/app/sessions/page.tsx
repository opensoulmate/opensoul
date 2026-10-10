'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, MessageSquare, Trash2, Edit3, Tag, X, ChevronLeft,
  ChevronRight, RefreshCw, Clock, Hash, User, Bot, Send
} from 'lucide-react';

interface Session {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  source: string;
  message_count: number;
  input_tokens: number;
  output_tokens: number;
  tags?: string[];
  estimated_cost_usd?: number;
}

interface Message {
  id: string;
  role: string;
  content: string;
  timestamp: string;
  source: string;
}

export default function SessionsPage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);
  const limit = 30;

  // Detail view
  const [selectedSession, setSelectedSession] = useState<Session | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [showDetail, setShowDetail] = useState(false);

  // Edit
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');

  // Tag
  const [tagInput, setTagInput] = useState('');

  const fetchSessions = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiFetch(`/api/sessions?limit=${limit}&offset=${page * limit}`);
      setSessions(Array.isArray(data.sessions) ? data.sessions : []);
      setTotal(data.total || 0);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [page]);

  const fetchMessages = useCallback(async (sessionId: string) => {
    try {
      setMessagesLoading(true);
      const data = await apiFetch(`/api/sessions/${sessionId}/messages`);
      setMessages(Array.isArray(data.messages) ? data.messages : []);
    } catch (e: any) {
      setMessages([]);
    } finally {
      setMessagesLoading(false);
    }
  }, []);

  useEffect(() => { fetchSessions(); }, [fetchSessions]);

  const handleSearch = async () => {
    if (!search.trim()) { fetchSessions(); return; }
    try {
      setLoading(true);
      const data = await apiFetch(`/api/sessions/search?q=${encodeURIComponent(search)}`);
      setSessions(Array.isArray(data.sessions) ? data.sessions : []);
      setTotal(data.sessions?.length || 0);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleRename = async (sessionId: string) => {
    if (!editTitle.trim()) return;
    try {
      await apiFetch(`/api/sessions/${sessionId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: editTitle }),
      });
      setEditingId(null);
      fetchSessions();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleDelete = async (sessionId: string) => {
    if (!confirm('确定要删除该会话及其所有消息？')) return;
    try {
      await apiFetch(`/api/sessions/${sessionId}`, { method: 'DELETE' });
      if (showDetail && selectedSession?.id === sessionId) {
        setShowDetail(false);
      }
      fetchSessions();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleAddTag = async (sessionId: string) => {
    if (!tagInput.trim()) return;
    try {
      await apiFetch(`/api/sessions/${sessionId}/tags`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tag_name: tagInput.trim() }),
      });
      setTagInput('');
      fetchSessions();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleRemoveTag = async (sessionId: string, tagName: string) => {
    try {
      await apiFetch(`/api/sessions/${sessionId}/tags/${tagName}`, { method: 'DELETE' });
      fetchSessions();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const openDetail = (session: Session) => {
    setSelectedSession(session);
    setShowDetail(true);
    fetchMessages(session.id);
  };

  const totalPages = Math.ceil(total / limit);

  const formatTime = (iso: string) => {
    if (!iso) return '-';
    try {
      const d = new Date(iso);
      return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
    } catch { return iso; }
  };

  const formatTokens = (n: number) => {
    if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
    if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
    return String(n);
  };

  if (loading && sessions.length === 0) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;
  if (error && sessions.length === 0) return <div className="text-sm text-red-500 p-4">错误: {error}</div>;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{total}</div>
          <div className="text-xs text-muted-foreground">总会话数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{formatTokens(sessions.reduce((s, x) => s + (x.input_tokens || 0), 0))}</div>
          <div className="text-xs text-muted-foreground">输入Token</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{formatTokens(sessions.reduce((s, x) => s + (x.output_tokens || 0), 0))}</div>
          <div className="text-xs text-muted-foreground">输出Token</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索会话标题、消息内容..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
          />
        </div>
        <button onClick={handleSearch} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm border border-border rounded-lg hover:bg-muted transition-colors">
          <Search className="w-3.5 h-3.5" />搜索
        </button>
        <button onClick={() => { setSearch(''); fetchSessions(); }} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm border border-border rounded-lg hover:bg-muted transition-colors">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </button>
      </div>

      {/* Sessions table */}
      <div className="rounded-lg border border-border bg-card overflow-hidden">
        <table className="w-full text-xs lg:text-sm min-w-[500px]">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">标题</th>
              <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground w-24">来源</th>
              <th className="text-center px-2 lg:px-4 py-2.5 font-medium text-muted-foreground w-16">消息</th>
              <th className="text-right px-2 lg:px-4 py-2.5 font-medium text-muted-foreground w-28">Token (入/出)</th>
              <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground w-28">更新时间</th>
              <th className="text-right px-2 lg:px-4 py-2.5 font-medium text-muted-foreground w-24">操作</th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((s) => (
              <tr key={s.id} className="border-b border-border last:border-0 hover:bg-muted/30 cursor-pointer" onClick={() => openDetail(s)}>
                <td className="px-2 lg:px-4 py-2.5">
                  {editingId === s.id ? (
                    <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                      <input
                        className="flex-1 px-2 py-0.5 text-xs bg-muted border border-border rounded"
                        value={editTitle}
                        onChange={(e) => setEditTitle(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && handleRename(s.id)}
                        autoFocus
                      />
                      <button onClick={() => handleRename(s.id)} className="text-xs lg:text-sm text-primary hover:underline">保存</button>
                      <button onClick={() => setEditingId(null)} className="text-xs lg:text-sm text-muted-foreground hover:underline">取消</button>
                    </div>
                  ) : (
                    <div>
                      <div className="text-xs font-medium truncate max-w-[300px]">{s.title || '无标题'}</div>
                      {s.tags && s.tags.length > 0 && (
                        <div className="flex items-center gap-1 mt-1">
                          {s.tags.map((t) => (
                            <span key={t} className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-primary/10 text-primary">{t}</span>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </td>
                <td className="px-2 lg:px-4 py-2.5">
                  <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-muted text-muted-foreground">{s.source || '-'}</span>
                </td>
                <td className="px-2 lg:px-4 py-2.5 text-center text-xs">{s.message_count}</td>
                <td className="px-2 lg:px-4 py-2.5 text-right text-xs text-muted-foreground">
                  {formatTokens(s.input_tokens)} / {formatTokens(s.output_tokens)}
                </td>
                <td className="px-2 lg:px-4 py-2.5 text-xs text-muted-foreground">{formatTime(s.updated_at)}</td>
                <td className="px-2 lg:px-4 py-2.5 text-right" onClick={(e) => e.stopPropagation()}>
                  <div className="flex items-center justify-end gap-1">
                    <button
                      onClick={() => { setEditingId(s.id); setEditTitle(s.title || ''); }}
                      className="p-1 rounded-md hover:bg-muted"
                      title="重命名"
                    >
                      <Edit3 className="w-3 h-3 text-muted-foreground" />
                    </button>
                    <button
                      onClick={() => handleDelete(s.id)}
                      className="p-1 rounded-md hover:bg-red-500/10"
                      title="删除"
                    >
                      <Trash2 className="w-3 h-3 text-red-500" />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {sessions.length === 0 && (
          <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
            <MessageSquare className="w-10 h-10 mb-2 opacity-30" />
            <p className="text-xs">暂无会话记录</p>
          </div>
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>共 {total} 条，第 {page + 1}/{totalPages} 页</span>
          <div className="flex items-center gap-1">
            <button
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              className="p-1.5 rounded-md border border-border hover:bg-muted disabled:opacity-30"
            >
              <ChevronLeft className="w-3.5 h-3.5" />
            </button>
            <button
              disabled={page >= totalPages - 1}
              onClick={() => setPage((p) => p + 1)}
              className="p-1.5 rounded-md border border-border hover:bg-muted disabled:opacity-30"
            >
              <ChevronRight className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      )}

      {/* Detail dialog */}
      {showDetail && selectedSession && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowDetail(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-2xl mx-4 max-h-[85vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            {/* Header */}
            <div className="flex items-center justify-between p-4 border-b border-border shrink-0">
              <div className="min-w-0 flex-1">
                <h2 className="text-sm font-medium truncate">{selectedSession.title || '无标题'}</h2>
                <div className="flex items-center gap-3 mt-1 text-[10px] text-muted-foreground">
                  <span>ID: {selectedSession.id}</span>
                  <span>来源: {selectedSession.source || '-'}</span>
                  <span>消息: {selectedSession.message_count}</span>
                </div>
              </div>
              <button onClick={() => setShowDetail(false)} className="p-1 rounded-md hover:bg-muted shrink-0 ml-2">
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Tags section */}
            <div className="px-4 py-2 border-b border-border flex items-center gap-2 shrink-0">
              <Tag className="w-3 h-3 text-muted-foreground" />
              {selectedSession.tags?.map((t) => (
                <span key={t} className="flex items-center gap-0.5 rounded-full px-2 py-0.5 text-[10px] font-medium bg-primary/10 text-primary">
                  {t}
                  <button onClick={() => handleRemoveTag(selectedSession.id, t)} className="hover:text-red-500">
                    <X className="w-2.5 h-2.5" />
                  </button>
                </span>
              ))}
              <input
                className="flex-1 min-w-[80px] px-2 py-0.5 text-[10px] bg-muted border border-border rounded"
                placeholder="添加标签..."
                value={tagInput}
                onChange={(e) => setTagInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleAddTag(selectedSession.id)}
              />
            </div>

            {/* Messages */}
            <div className="flex-1 overflow-auto p-4 space-y-3 min-h-0">
              {messagesLoading ? (
                <div className="text-xs text-muted-foreground text-center py-8">加载消息中...</div>
              ) : messages.length === 0 ? (
                <div className="text-xs text-muted-foreground text-center py-8">暂无消息</div>
              ) : (
                messages.map((m) => (
                  <div key={m.id} className={`flex gap-2 ${m.role === 'user' ? 'justify-end' : ''}`}>
                    {m.role !== 'user' && (
                      <div className="w-6 h-6 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                        <Bot className="w-3 h-3 text-primary" />
                      </div>
                    )}
                    <div className={`max-w-[80%] rounded-lg px-3 py-2 text-xs ${m.role === 'user' ? 'bg-primary text-primary-foreground' : 'bg-muted'}`}>
                      <div className="whitespace-pre-wrap break-words">{m.content.slice(0, 2000)}{m.content.length > 2000 ? '...' : ''}</div>
                      <div className={`text-[10px] mt-1 ${m.role === 'user' ? 'text-primary-foreground/60' : 'text-muted-foreground'}`}>
                        {formatTime(m.timestamp)}
                      </div>
                    </div>
                    {m.role === 'user' && (
                      <div className="w-6 h-6 rounded-full bg-orange-500/10 flex items-center justify-center shrink-0">
                        <User className="w-3 h-3 text-orange-500" />
                      </div>
                    )}
                  </div>
                ))
              )}
            </div>

            {/* Footer actions */}
            <div className="flex items-center gap-2 p-4 border-t border-border shrink-0">
              <button
                onClick={() => { setEditingId(selectedSession.id); setEditTitle(selectedSession.title || ''); setShowDetail(false); }}
                className="flex items-center gap-1 px-3 py-1.5 text-xs lg:text-sm border border-border rounded-lg hover:bg-muted transition-colors"
              >
                <Edit3 className="w-3 h-3" />重命名
              </button>
              <button
                onClick={() => { handleDelete(selectedSession.id); }}
                className="flex items-center gap-1 px-3 py-1.5 text-xs lg:text-sm bg-red-500/10 text-red-500 rounded-lg hover:bg-red-500/20"
              >
                <Trash2 className="w-3 h-3" />删除会话
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
