'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, RefreshCw, X, Trash2, Eye, ChevronDown, ChevronRight,
  Globe, FileText, MousePointer, Clock, ExternalLink, ArrowUpCircle,
  AlertCircle, CheckCircle, Loader2, Filter, Camera,
} from 'lucide-react';

// ── Types ───────────────────────────────────────────────────

interface Capture {
  id: number;
  capture_type: string;
  title: string;
  url: string;
  description: string;
  keywords: string[];
  content: string;
  content_hash: string;
  status: string;
  created_at: number;
  user_id: string;
}

// ── Helpers ─────────────────────────────────────────────────

function formatTime(ts: number) {
  if (!ts) return '-';
  try {
    return new Date(ts * 1000).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    });
  } catch { return String(ts); }
}

function formatRelative(ts: number) {
  if (!ts) return '';
  const diff = Date.now() - ts * 1000;
  if (diff < 60000) return '刚刚';
  if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`;
  return `${Math.floor(diff / 86400000)}天前`;
}

function StatusBadge({ status }: { status: string }) {
  const config: Record<string, { color: string; label: string }> = {
    captured: { color: 'bg-blue-500/10 text-blue-500 border-blue-500/30', label: '已捕获' },
    promoted: { color: 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30', label: '已提升' },
    duplicate: { color: 'bg-amber-500/10 text-amber-500 border-amber-500/30', label: '重复' },
  };
  const c = config[status] || { color: 'bg-muted text-muted-foreground border-border', label: status };
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium border ${c.color}`}>
      {c.label}
    </span>
  );
}

// ── Main Page ───────────────────────────────────────────────

export default function CapturePage() {
  const [captures, setCaptures] = useState<Capture[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState('all');
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [page, setPage] = useState(0);
  const [promoting, setPromoting] = useState<number | null>(null);
  const pageSize = 20;

  const [stats, setStats] = useState({ total: 0, pages: 0, selections: 0, recent_24h: 0 });

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError('');
    const results = await Promise.allSettled([
      apiFetch(`/api/capture/list?limit=${pageSize}&offset=${page * pageSize}&capture_type=${typeFilter === 'all' ? '' : typeFilter}`),
      apiFetch('/api/capture/stats'),
    ]);
    if (results[0].status === 'fulfilled') {
      setCaptures(results[0].value.captures || []);
      setTotal(results[0].value.total || 0);
    }
    if (results[1].status === 'fulfilled') {
      setStats({
        total: results[1].value.total_captures || 0,
        pages: results[1].value.page_captures || 0,
        selections: results[1].value.selection_captures || 0,
        recent_24h: results[1].value.recent_24h || 0,
      });
    }
    const errors = results.filter(r => r.status === 'rejected');
    if (errors.length === results.length) setError('请求失败');
    setLoading(false);
  }, [page, typeFilter]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleDelete = async (id: number) => {
    if (!confirm('确定要删除此捕获？')) return;
    try {
      await apiFetch(`/api/capture/${id}`, { method: 'DELETE' });
      fetchData();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handlePromote = async (id: number) => {
    try {
      setPromoting(id);
      await apiFetch(`/api/capture/${id}/promote`, { method: 'POST' });
      fetchData();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setPromoting(null);
    }
  };

  const filtered = captures.filter((c) => {
    if (search) {
      const q = search.toLowerCase();
      return (c.title || '').toLowerCase().includes(q) ||
        (c.url || '').toLowerCase().includes(q) ||
        (c.content || '').toLowerCase().includes(q);
    }
    return true;
  });

  const totalPages = Math.ceil(total / pageSize);

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* ── Stats ── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{stats.total}</div>
          <div className="text-xs text-muted-foreground">总捕获数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <Globe className="w-3.5 h-3.5 text-blue-500" />
          </div>
          <div className="text-2xl font-bold text-blue-500">{stats.pages}</div>
          <div className="text-xs text-muted-foreground">整页捕获</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <MousePointer className="w-3.5 h-3.5 text-purple-500" />
          </div>
          <div className="text-2xl font-bold text-purple-500">{stats.selections}</div>
          <div className="text-xs text-muted-foreground">选区捕获</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-amber-500">{stats.recent_24h}</div>
          <div className="text-xs text-muted-foreground">近24小时</div>
        </div>
      </div>

      {/* ── Info Banner ── */}
      <div className="rounded-lg border border-blue-500/30 bg-blue-500/5 p-3 flex items-center gap-2">
        <Camera className="w-4 h-4 text-blue-500 shrink-0" />
        <div>
          <span className="text-xs font-medium text-blue-500">浏览器扩展捕获</span>
          <p className="text-[10px] text-muted-foreground mt-0.5">
            通过OpenMate浏览器扩展捕获的网页内容和选区文本。可提升为知识库条目。
          </p>
        </div>
      </div>

      {/* ── Toolbar ── */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索标题、URL、内容..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={typeFilter}
          onChange={(e) => { setTypeFilter(e.target.value); setPage(0); }}
        >
          <option value="all">全部类型</option>
          <option value="page">整页</option>
          <option value="selection">选区</option>
        </select>
        <button onClick={() => fetchData()} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm border border-border rounded-lg hover:bg-muted transition-colors">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </button>
      </div>

      {/* ── Capture List ── */}
      <div className="space-y-2">
        {filtered.map((capture) => (
          <div key={capture.id} className="border border-border rounded-lg bg-card hover:bg-muted/20 transition-colors">
            <button
              onClick={() => setExpandedId(expandedId === capture.id ? null : capture.id)}
              className="flex items-center gap-3 w-full p-3 text-left"
            >
              <div className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${
                capture.capture_type === 'page' ? 'bg-blue-500/10' : 'bg-purple-500/10'
              }`}>
                {capture.capture_type === 'page' ? (
                  <Globe className="w-5 h-5 text-blue-500" />
                ) : (
                  <MousePointer className="w-5 h-5 text-purple-500" />
                )}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-medium truncate">{capture.title || '无标题'}</span>
                  <StatusBadge status={capture.status} />
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
                    capture.capture_type === 'page' ? 'bg-blue-500/10 text-blue-500' : 'bg-violet-500/10 text-violet-500'
                  }`}>
                    {capture.capture_type === 'page' ? '整页' : '选区'}
                  </span>
                </div>
                <div className="flex items-center gap-3 mt-0.5 text-[10px] text-muted-foreground">
                  {capture.url && (
                    <span className="flex items-center gap-1 truncate max-w-[300px]">
                      <ExternalLink className="w-2.5 h-2.5" />{capture.url}
                    </span>
                  )}
                  <span className="flex items-center gap-1 shrink-0">
                    <Clock className="w-2.5 h-2.5" />{formatRelative(capture.created_at)}
                  </span>
                </div>
              </div>
              <div className="text-right shrink-0 mr-2">
                <div className="text-[10px] text-muted-foreground">#{capture.id}</div>
                <div className="text-[10px] text-muted-foreground">{formatTime(capture.created_at)}</div>
              </div>
              {expandedId === capture.id ? <ChevronDown className="w-3.5 h-3.5 shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 shrink-0" />}
            </button>

            {expandedId === capture.id && (
              <div className="px-3 pb-3 border-t border-border pt-3 space-y-3">
                {/* Content preview */}
                <div className="rounded-lg border border-border bg-muted/30 p-3">
                  <div className="text-[10px] text-muted-foreground mb-1">内容预览</div>
                  <p className="text-xs text-muted-foreground whitespace-pre-wrap max-h-[200px] overflow-auto">
                    {(capture.content || '').slice(0, 2000) || '无内容'}
                  </p>
                </div>

                {/* Keywords */}
                {capture.keywords && capture.keywords.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {capture.keywords.map((kw, i) => (
                      <span key={i} className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-primary/10 text-primary">{kw}</span>
                    ))}
                  </div>
                )}

                {/* Actions */}
                <div className="flex items-center gap-2 pt-2 border-t border-border">
                  {capture.status !== 'promoted' && (
                    <button
                      onClick={() => handlePromote(capture.id)}
                      disabled={promoting === capture.id}
                      className="flex items-center gap-1 px-2.5 py-1 text-[10px] bg-emerald-500/10 text-emerald-500 rounded-lg hover:bg-emerald-500/20 disabled:opacity-50"
                    >
                      {promoting === capture.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <ArrowUpCircle className="w-3 h-3" />}
                      提升为知识
                    </button>
                  )}
                  {capture.status === 'promoted' && (
                    <span className="flex items-center gap-1 text-[10px] text-green-500">
                      <CheckCircle className="w-3 h-3" />已提升至知识库
                    </span>
                  )}
                  {capture.url && (
                    <a
                      href={capture.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-1 px-2.5 py-1 text-[10px] bg-muted border border-border rounded hover:bg-muted/80"
                    >
                      <ExternalLink className="w-3 h-3" />打开原文
                    </a>
                  )}
                  <button
                    onClick={() => handleDelete(capture.id)}
                    className="flex items-center gap-1 px-2.5 py-1 text-[10px] bg-red-500/10 text-red-500 rounded-lg hover:bg-red-500/20 ml-auto"
                  >
                    <Trash2 className="w-3 h-3" />删除
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* ── Pagination ── */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted-foreground">第 {page + 1} / {totalPages} 页，共 {total} 条</span>
          <div className="flex items-center gap-1">
            <button onClick={() => setPage(Math.max(0, page - 1))} disabled={page === 0}
              className="px-3 py-1.5 text-xs lg:text-sm border border-border rounded-lg hover:bg-muted disabled:opacity-30 transition-colors">上一页</button>
            <button onClick={() => setPage(Math.min(totalPages - 1, page + 1))} disabled={page >= totalPages - 1}
              className="px-3 py-1.5 text-xs lg:text-sm border border-border rounded-lg hover:bg-muted disabled:opacity-30 transition-colors">下一页</button>
          </div>
        </div>
      )}

      {filtered.length === 0 && (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
          <Camera className="w-10 h-10 mb-2 opacity-30" />
          <p className="text-xs">暂无捕获记录</p>
          <p className="text-[10px] mt-1">安装OpenMate浏览器扩展后可自动捕获网页内容</p>
        </div>
      )}

      {/* ── Error ── */}
      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3 flex items-center gap-2">
          <AlertCircle className="w-4 h-4 text-red-500 shrink-0" />
          <span className="text-xs text-red-500 flex-1">{error}</span>
          <button onClick={() => setError('')} className="p-1 hover:bg-muted rounded-md"><X className="w-3 h-3" /></button>
        </div>
      )}
    </div>
  );
}
