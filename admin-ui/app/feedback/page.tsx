'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, RefreshCw, X, FileText, Sparkles, Brain, Zap,
  BarChart3, Tag, Lightbulb, GitBranch, BookOpen
} from 'lucide-react';
import { Badge, TONES, tone } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

interface FeedbackEntry {
  id: string;
  user_id: string;
  title: string;
  content: string;
  feedback_type: string;
  confidence: string;
  evidence: string;
  source: string;
  tags: string[];
  created_at: string;
}

interface ExtractResult {
  entries: { type: string; title: string; content: string; confidence: string; evidence: string; tags: string[] }[];
  stored_count: number;
  skipped_count: number;
}

export default function FeedbackPage() {
  const [entries, setEntries] = useState<FeedbackEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [filterType, setFilterType] = useState('');
  const [stats, setStats] = useState<any>(null);

  // Extract dialog
  const [showExtract, setShowExtract] = useState(false);
  const [extractText, setExtractText] = useState('');
  const [extractSource, setExtractSource] = useState('hermes');
  const [extractLoading, setExtractLoading] = useState(false);
  const [extractResult, setExtractResult] = useState<ExtractResult | null>(null);

  // Detail
  const [selected, setSelected] = useState<FeedbackEntry | null>(null);
  const [showDetail, setShowDetail] = useState(false);

  const fetchEntries = useCallback(async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams({ user_id: '00000000-0000-0000-0000-000000000000', limit: '100' });
      if (filterType) params.set('type', filterType);
      const data = await apiFetch(`/api/feedback/entries?${params}`);
      setEntries(Array.isArray(data) ? data : []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [filterType]);

  const fetchStats = useCallback(async () => {
    try {
      const data = await apiFetch('/api/feedback/stats?user_id=00000000-0000-0000-0000-000000000000');
      setStats(data);
    } catch { /* silent */ }
  }, []);

  useEffect(() => { fetchEntries(); fetchStats(); }, [fetchEntries, fetchStats]);

  const handleExtract = async () => {
    if (!extractText.trim()) return;
    try {
      setExtractLoading(true);
      const data = await apiFetch('/api/feedback/extract?user_id=00000000-0000-0000-0000-000000000000', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conversation: extractText, source: extractSource }),
      });
      setExtractResult(data);
      if (data.stored_count > 0) {
        await fetchEntries();
        await fetchStats();
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setExtractLoading(false);
    }
  };

  const filtered = entries.filter(e => {
    if (!search) return true;
    const q = search.toLowerCase();
    return e.title.toLowerCase().includes(q) || e.content.toLowerCase().includes(q) ||
      e.tags.some(t => t.toLowerCase().includes(q));
  });

  const typeIcon = (t: string) => {
    switch (t) {
      case 'knowledge': return <BookOpen className="w-3.5 h-3.5" />;
      case 'pattern': return <GitBranch className="w-3.5 h-3.5" />;
      case 'decision': return <Lightbulb className="w-3.5 h-3.5" />;
      default: return <FileText className="w-3.5 h-3.5" />;
    }
  };

  const typeLabel = (t: string) => {
    switch (t) {
      case 'knowledge': return '知识';
      case 'pattern': return '模式';
      case 'decision': return '决策';
      default: return t;
    }
  };

  const typeColor = (t: string) => {
    switch (t) {
      case 'knowledge': return tone('info');
      case 'pattern': return tone('success');
      case 'decision': return tone('accent');
      default: return tone('muted');
    }
  };

  const confidenceColor = (c: string) => {
    switch (c) {
      case 'high': return TONES.success.text;
      case 'medium': return TONES.warning.text;
      case 'low': return TONES.destructive.text;
      default: return TONES.muted.text;
    }
  };

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-500/10 text-red-500 text-xs">
          {error}
          <Button variant="bare" onClick={() => setError('')} className="ml-auto"><X className="w-3.5 h-3.5" /></Button>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{stats?.total ?? entries.length}</div>
          <div className="text-xs text-muted-foreground">知识条目</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-500">{stats?.by_type?.knowledge ?? 0}</div>
          <div className="text-xs text-muted-foreground">知识</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-600">{stats?.by_type?.pattern ?? 0}</div>
          <div className="text-xs text-muted-foreground">模式</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-purple-500">{stats?.by_type?.decision ?? 0}</div>
          <div className="text-xs text-muted-foreground">决策</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" placeholder="搜索标题、内容、标签..." value={search} onChange={e => setSearch(e.target.value)} />
        </div>
        <select className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={filterType} onChange={e => setFilterType(e.target.value)}>
          <option value="">全部类型</option>
          <option value="knowledge">知识</option>
          <option value="pattern">模式</option>
          <option value="decision">决策</option>
        </select>
        <Button onClick={() => { setExtractResult(null); setExtractText(''); setShowExtract(true); }} variant="chip-sm" className="flex items-center gap-1.5 bg-violet-500/10 text-violet-500 border border-border hover:bg-violet-500/20">
          <Sparkles className="w-3.5 h-3.5" />知识提取
        </Button>
        <Button onClick={() => { fetchEntries(); fetchStats(); }} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
      </div>

      {/* Confidence distribution */}
      {stats?.by_confidence && (
        <div className="flex items-center gap-3 text-xs">
          <span className="text-muted-foreground">置信度分布:</span>
          {Object.entries(stats.by_confidence).map(([conf, cnt]) => (
            <span key={conf} className={`font-medium ${confidenceColor(conf)}`}>{conf}: {String(cnt)}</span>
          ))}
        </div>
      )}

      {/* Entries list */}
      <div className="space-y-2">
        {filtered.map(e => (
          <div
            key={e.id}
            className="rounded-lg border border-border bg-card p-4 hover:bg-muted/30 transition-colors cursor-pointer"
            onClick={() => { setSelected(e); setShowDetail(true); }}
          >
            <div className="flex items-start gap-3">
              <div className="mt-0.5">{typeIcon(e.feedback_type)}</div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-medium">{e.title}</h3>
                  <Badge variant="bare" className={`${typeColor(e.feedback_type)}`}>{typeLabel(e.feedback_type)}</Badge>
                  <span className={`text-[10px] ${confidenceColor(e.confidence)}`}>● {e.confidence}</span>
                </div>
                <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{e.content}</p>
                <div className="flex items-center gap-2 mt-2">
                  {e.tags.slice(0, 5).map(t => <Badge variant="muted" key={t} className="text-inherit">{t}</Badge>)}
                  <span className="text-[10px] text-muted-foreground ml-auto">{e.source} · {e.created_at ? new Date(e.created_at).toLocaleDateString() : '—'}</span>
                </div>
              </div>
            </div>
          </div>
        ))}
        {filtered.length === 0 && (
          <div className="text-center text-sm text-muted-foreground py-8">暂无知识条目</div>
        )}
      </div>

      {/* Detail Dialog */}
      {showDetail && selected && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowDetail(false)}>
          <div className="bg-card rounded-lg border border-border p-6 max-w-lg w-full mx-4 max-h-[80vh] overflow-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                {typeIcon(selected.feedback_type)}
                <h2 className="text-lg font-medium">{selected.title}</h2>
              </div>
              <Button variant="bare" onClick={() => setShowDetail(false)}><X className="w-4 h-4 text-muted-foreground" /></Button>
            </div>
            <div className="space-y-3 text-sm">
              <div className="flex items-center gap-2">
                <Badge className={`${typeColor(selected.feedback_type)}`}>{typeLabel(selected.feedback_type)}</Badge>
                <span className={`text-xs ${confidenceColor(selected.confidence)}`}>置信度: {selected.confidence}</span>
              </div>
              <div><span className="text-muted-foreground">内容: </span>{selected.content}</div>
              {selected.evidence && (
                <div className="p-3 bg-muted/50 rounded-lg">
                  <div className="text-xs text-muted-foreground mb-1">证据:</div>
                  <div className="text-xs">{selected.evidence}</div>
                </div>
              )}
              {selected.tags.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {selected.tags.map(t => <Badge key={t} variant="subtle">{t}</Badge>)}
                </div>
              )}
              <div className="text-xs text-muted-foreground">
                <div>来源: {selected.source}</div>
                <div>ID: <code className="bg-muted px-1 rounded">{selected.id}</code></div>
                <div>创建: {selected.created_at}</div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Extract Dialog */}
      {showExtract && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowExtract(false)}>
          <div className="bg-card rounded-lg border border-border p-6 max-w-2xl w-full mx-4 max-h-[80vh] overflow-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <Sparkles className="w-5 h-5 text-purple-500" />
                <h2 className="text-lg font-medium">知识提取</h2>
              </div>
              <Button variant="bare" onClick={() => setShowExtract(false)}><X className="w-4 h-4 text-muted-foreground" /></Button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">对话内容 *</label>
                <textarea
                  className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 h-40 resize-none font-mono"
                  value={extractText} onChange={e => setExtractText(e.target.value)}
                  placeholder="粘贴AI对话内容，系统将自动提取知识、模式和决策..."
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">来源</label>
                <input className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md" value={extractSource} onChange={e => setExtractSource(e.target.value)} placeholder="hermes" />
              </div>
              <Button onClick={handleExtract} disabled={extractLoading || !extractText.trim()} variant="chip-sm" className="flex items-center gap-1.5 bg-violet-500/10 text-violet-500 border border-border hover:bg-violet-500/20 disabled:opacity-50">
                {extractLoading ? <Zap className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
                {extractLoading ? '提取中...' : '开始提取'}
              </Button>

              {extractResult && (
                <div className="mt-4 space-y-3">
                  <div className="flex items-center gap-3 text-xs">
                    <span className="text-green-600">存储: {extractResult.stored_count}</span>
                    <span className="text-muted-foreground">跳过: {extractResult.skipped_count}</span>
                    <span className="text-muted-foreground">提取: {extractResult.entries.length}</span>
                  </div>
                  {extractResult.entries.map((entry, i) => (
                    <div key={i} className="p-3 rounded-lg bg-muted/50 space-y-2">
                      <div className="flex items-center gap-2">
                        <Badge className={`${typeColor(entry.type)}`}>{typeLabel(entry.type)}</Badge>
                        <span className={`text-xs ${confidenceColor(entry.confidence)}`}>{entry.confidence}</span>
                        <span className="text-sm font-medium">{entry.title}</span>
                      </div>
                      <p className="text-xs text-muted-foreground">{entry.content}</p>
                      {entry.evidence && <div className="text-xs text-muted-foreground italic">证据: {entry.evidence}</div>}
                      <div className="flex flex-wrap gap-1">
                        {entry.tags.map(t => <Badge key={t} variant="subtle">{t}</Badge>)}
                      </div>
                    </div>
                  ))}
                  {extractResult.entries.length === 0 && (
                    <div className="text-xs text-muted-foreground text-center py-4">未从对话中提取到知识</div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
