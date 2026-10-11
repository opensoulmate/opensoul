'use client';

import { useState, useCallback, useRef } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, RefreshCw, X, BookOpen, FileText, Radio, Bot, GraduationCap,
  BarChart3, Timer, Dna, Volume2, Database, Filter, ChevronDown
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

// ── Types ─────────────────────────────────────────────────────

interface SearchResult {
  source: string;
  icon: string;
  title: string;
  snippet: string;
  [key: string]: unknown;
}

interface UnifiedResponse {
  query: string;
  total: number;
  by_source: Record<string, SearchResult[]>;
  sources_searched: string[];
}

interface KnowledgeResult {
  query: string;
  mode: string;
  results: { title: string; content: string; score?: number }[];
}

// ── Source config ─────────────────────────────────────────────

const SOURCES = [
  { key: 'knowledge', label: '知识库', icon: <BookOpen className="w-4 h-4" />, color: 'text-blue-400' },
  { key: 'files', label: '文件', icon: <FileText className="w-4 h-4" />, color: 'text-green-400' },
  { key: 'events', label: '事件', icon: <Radio className="w-4 h-4" />, color: 'text-yellow-400' },
  { key: 'agents', label: 'Agent', icon: <Bot className="w-4 h-4" />, color: 'text-purple-400' },
  { key: 'courses', label: '课程', icon: <GraduationCap className="w-4 h-4" />, color: 'text-pink-400' },
  { key: 'trajectory', label: '轨迹', icon: <BarChart3 className="w-4 h-4" />, color: 'text-orange-400' },
  { key: 'cron', label: '定时任务', icon: <Timer className="w-4 h-4" />, color: 'text-cyan-400' },
  { key: 'gene', label: '基因模板', icon: <Dna className="w-4 h-4" />, color: 'text-red-400' },
  { key: 'echo', label: '消息', icon: <Volume2 className="w-4 h-4" />, color: 'text-teal-400' },
];

// ── Component ─────────────────────────────────────────────────

export default function SearchPage() {
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<'unified' | 'semantic' | 'fulltext' | 'hybrid'>('unified');
  const [results, setResults] = useState<UnifiedResponse | null>(null);
  const [kbResults, setKbResults] = useState<KnowledgeResult | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState('');
  const [selectedSources, setSelectedSources] = useState<Set<string>>(new Set(['all']));
  const [showSourceFilter, setShowSourceFilter] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleSearch = useCallback(async () => {
    if (!query.trim()) return;
    setSearching(true);
    setError('');
    setResults(null);
    setKbResults(null);

    try {
      if (mode === 'unified') {
        const sources = selectedSources.has('all') ? 'all' : Array.from(selectedSources).join(',');
        const data = await apiFetch(`/api/search/unified?q=${encodeURIComponent(query)}&sources=${sources}&limit=10`);
        setResults(data as UnifiedResponse);
      } else {
        const data = await apiFetch(`/api/search/?q=${encodeURIComponent(query)}&mode=${mode}&limit=10`);
        setKbResults(data as KnowledgeResult);
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSearching(false);
    }
  }, [query, mode, selectedSources]);

  const toggleSource = (key: string) => {
    setSelectedSources(prev => {
      const next = new Set(prev);
      if (key === 'all') {
        return new Set(['all']);
      }
      next.delete('all');
      if (next.has(key)) {
        next.delete(key);
        if (next.size === 0) next.add('all');
      } else {
        next.add(key);
      }
      return next;
    });
  };

  const sourceColor = (src: string) => {
    return SOURCES.find(s => s.key === src)?.color || 'text-muted-foreground';
  };

  const sourceLabel = (src: string) => {
    return SOURCES.find(s => s.key === src)?.label || src;
  };

  return (
    <div className="space-y-6">
      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
          <span className="flex-1">{error}</span>
          <Button variant="bare" onClick={() => setError('')}><X className="w-4 h-4" /></Button>
        </div>
      )}

      {/* Search Input */}
      <div className="rounded-xl border border-border bg-card p-5 space-y-4">
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <input
              ref={inputRef}
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSearch()}
              placeholder="搜索知识、文件、事件、Agent、课程..."
              className="w-full pl-9 pr-3 py-2.5 text-sm rounded-lg bg-muted border border-border focus:outline-none focus:border-primary"
              autoFocus
            />
          </div>
          <Button onClick={handleSearch} disabled={searching} variant="primary" className="px-5 py-2.5 text-sm">
            {searching ? '搜索中...' : '搜索'}
          </Button>
        </div>

        {/* Mode + Source Filter */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-1">
            <span className="text-xs text-muted-foreground mr-1">模式:</span>
            {[
              { key: 'unified' as const, label: '统一搜索' },
              { key: 'hybrid' as const, label: '混合' },
              { key: 'semantic' as const, label: '语义' },
              { key: 'fulltext' as const, label: '全文' },
            ].map(m => (
              <Button variant="bare"
                key={m.key}
                onClick={() => setMode(m.key)}
                className={`px-2.5 py-1 text-xs lg:text-sm rounded-lg border transition-colors ${
                  mode === m.key
                    ? 'bg-primary/10 text-primary border-primary/20'
                    : 'bg-muted text-muted-foreground border-border hover:text-foreground'
                }`}
              >
                {m.label}
              </Button>
            ))}
          </div>

          {mode === 'unified' && (
            <div className="relative">
              <Button onClick={() => setShowSourceFilter(!showSourceFilter)} variant="secondary" className="px-2.5 py-1">
                <Filter className="w-3 h-3" />
                来源
                <ChevronDown className="w-3 h-3" />
              </Button>
              {showSourceFilter && (
                <div className="absolute top-full left-0 mt-1 z-10 w-48 rounded-lg border border-border bg-card shadow-lg p-2 space-y-1">
                  <Button variant="bare"
                    onClick={() => toggleSource('all')}
                    className={`w-full text-left px-2 py-1.5 text-xs lg:text-sm rounded-lg ${selectedSources.has('all') ? 'bg-primary/10 text-primary' : 'hover:bg-muted'}`}
                  >
                    全部来源
                  </Button>
                  {SOURCES.map(s => (
                    <Button
                      key={s.key}
                      onClick={() => toggleSource(s.key)}
                      variant="row" className={`gap-2 px-2 py-1.5 text-xs lg:text-sm rounded-lg  ${selectedSources.has(s.key) ? 'bg-primary/10 text-primary' : 'hover:bg-muted'}`}
                    >
                      <span className={s.color}>{s.icon}</span>
                      {s.label}
                    </Button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Results: Unified Mode */}
      {results && (
        <div className="space-y-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <span>找到 <strong className="text-foreground">{results.total}</strong> 条结果</span>
            <span>·</span>
            <span>搜索了 {results.sources_searched.length} 个来源</span>
          </div>

          {results.total === 0 ? (
            <div className="text-sm text-muted-foreground p-8 text-center">未找到匹配结果</div>
          ) : (
            Object.entries(results.by_source).map(([source, items]) => {
              if (!items || items.length === 0) return null;
              return (
                <div key={source} className="rounded-xl border border-border bg-card overflow-hidden">
                  <div className="flex items-center gap-2 px-4 py-2.5 bg-muted/30 border-b border-border">
                    <span className={sourceColor(source)}>
                      {SOURCES.find(s => s.key === source)?.icon || <Database className="w-4 h-4" />}
                    </span>
                    <span className="text-sm font-medium">{sourceLabel(source)}</span>
                    <span className="text-xs text-muted-foreground">({items.length})</span>
                  </div>
                  <div className="divide-y divide-border/50">
                    {items.map((item, i) => (
                      <div key={i} className="px-4 py-3 hover:bg-muted/20 transition-colors">
                        <div className="flex items-start gap-2">
                          <span className="text-base mt-0.5">{item.icon}</span>
                          <div className="flex-1 min-w-0">
                            <div className="text-sm font-medium truncate">{item.title}</div>
                            <div className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{item.snippet}</div>
                            {/* Extra metadata */}
                            <div className="flex flex-wrap gap-2 mt-1.5">
                              {Object.entries(item).filter(([k]) => !['source','icon','title','snippet'].includes(k) && item[k] != null).map(([k, v]) => (
                                <Badge variant="muted" key={k}>{k}: {String(v).slice(0, 20)}</Badge>
                              ))}
                            </div>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })
          )}
        </div>
      )}

      {/* Results: Knowledge Mode */}
      {kbResults && (
        <div className="space-y-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <span>模式: <strong className="text-foreground">{kbResults.mode}</strong></span>
            <span>·</span>
            <span>找到 <strong className="text-foreground">{kbResults.results?.length || 0}</strong> 条结果</span>
          </div>

          {(!kbResults.results || kbResults.results.length === 0) ? (
            <div className="text-sm text-muted-foreground p-8 text-center">未找到匹配结果</div>
          ) : (
            <div className="rounded-xl border border-border bg-card divide-y divide-border/50">
              {kbResults.results.map((r, i) => (
                <div key={i} className="px-4 py-3 hover:bg-muted/20 transition-colors">
                  <div className="flex items-start gap-2">
                    <span className="text-base mt-0.5">📚</span>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium">{r.title}</div>
                      <div className="text-xs text-muted-foreground mt-0.5 line-clamp-3">{r.content?.slice(0, 200)}</div>
                      {r.score !== undefined && (
                        <div className="text-[10px] text-primary mt-1">相关度: {(r.score * 100).toFixed(1)}%</div>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Empty state */}
      {!results && !kbResults && !searching && (
        <div className="text-center py-12">
          <Search className="w-12 h-12 mx-auto text-muted-foreground/30 mb-3" />
          <div className="text-sm text-muted-foreground">输入关键词开始搜索</div>
          <div className="text-xs text-muted-foreground/60 mt-1">支持统一搜索（跨9个子系统）或单独的知识库搜索</div>
        </div>
      )}
    </div>
  );
}
