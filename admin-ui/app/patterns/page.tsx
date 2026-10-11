'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, RefreshCw, X, ChevronDown, ChevronRight, ArrowRight,
  Lightbulb, AlertTriangle, Shield, Plus, Trash2, Filter,
  TrendingUp, BookOpen, Brain, Tag, Clock, CheckCircle,
} from 'lucide-react';
import { Badge, toneBordered } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

// ── Types ───────────────────────────────────────────────────

interface Pattern {
  id: string;
  title: string;
  content: string;
  metadata: {
    feedback_type?: string;
    confidence?: string;
    problem?: string;
    solution?: string;
    tags?: string[];
    source?: string;
    session_id?: string;
    created_at?: string;
  };
  created_at: string;
  updated_at?: string;
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

function parsePatternContent(content: string): { problem: string; solution: string; detail: string } {
  // Try to parse structured content
  const problemMatch = content.match(/(?:问题|problem|场景|场景描述)[:：]\s*([\s\S]*?)(?=(?:方案|solution|解决|解答|建议)[:：]|$)/i);
  const solutionMatch = content.match(/(?:方案|solution|解决|解答|建议|修复)[:：]\s*([\s\S]*?)(?=(?:问题|problem|场景)[:：]|$)/i);

  if (problemMatch || solutionMatch) {
    return {
      problem: problemMatch?.[1]?.trim() || '',
      solution: solutionMatch?.[1]?.trim() || '',
      detail: content,
    };
  }

  // Try arrow format: "problem → solution"
  const arrowMatch = content.match(/^([\s\S]+?)\s*(?:->|→|=>)\s*([\s\S]+)$/);
  if (arrowMatch) {
    return {
      problem: arrowMatch[1].trim(),
      solution: arrowMatch[2].trim(),
      detail: content,
    };
  }

  // Fallback: entire content as problem
  return { problem: content, solution: '', detail: content };
}

function ConfidenceBadge({ level }: { level?: string }) {
  const config: Record<string, { color: string; label: string }> = {
    high: { color: toneBordered('success'), label: '高置信' },
    medium: { color: toneBordered('warning'), label: '中置信' },
    low: { color: toneBordered('destructive'), label: '低置信' },
  };
  const c = config[level || ''] || { color: toneBordered('muted'), label: level || '未知' };
  return (
    <Badge variant="bare" className={`border ${c.color}`}>
      {c.label}
    </Badge>
  );
}

function PatternCard({ pattern, expanded, onToggle }: {
  pattern: Pattern;
  expanded: boolean;
  onToggle: () => void;
}) {
  const parsed = parsePatternContent(pattern.content || '');
  const meta = pattern.metadata || {};
  const problem = meta.problem || parsed.problem;
  const solution = meta.solution || parsed.solution;
  const tags = meta.tags || [];
  const confidence = meta.confidence || 'medium';

  return (
    <div className="border border-border rounded-lg bg-card hover:bg-muted/20 transition-colors">
      <Button variant="bare"
        onClick={onToggle}
        className="flex items-start gap-3 w-full p-3 text-left"
      >
        {/* Pattern icon */}
        <div className="w-10 h-10 rounded-lg bg-amber-500/10 flex items-center justify-center shrink-0 mt-0.5">
          <Lightbulb className="w-5 h-5 text-amber-500" />
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium">{pattern.title || '未命名模式'}</span>
            <ConfidenceBadge level={confidence} />
            {tags.slice(0, 3).map((tag) => (
              <Badge key={tag} variant="muted">
                {tag}
              </Badge>
            ))}
          </div>

          {/* Problem → Solution summary */}
          <div className="mt-1.5 flex items-start gap-2 text-xs">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-1 text-muted-foreground">
                <AlertTriangle className="w-3 h-3 text-red-400 shrink-0" />
                <span className="text-[10px] text-red-400 font-medium">问题</span>
              </div>
              <p className="text-muted-foreground line-clamp-1 mt-0.5">{problem || '-'}</p>
            </div>
            <ArrowRight className="w-4 h-4 text-muted-foreground/30 shrink-0 mt-3" />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-1 text-muted-foreground">
                <CheckCircle className="w-3 h-3 text-emerald-400 shrink-0" />
                <span className="text-[10px] text-emerald-400 font-medium">方案</span>
              </div>
              <p className="text-muted-foreground line-clamp-1 mt-0.5">{solution || '-'}</p>
            </div>
          </div>

          <div className="flex items-center gap-3 mt-1 text-[10px] text-muted-foreground">
            <span className="flex items-center gap-1">
              <Clock className="w-2.5 h-2.5" />{formatTime(pattern.created_at)}
            </span>
            {meta.source && <span>来源: {meta.source}</span>}
          </div>
        </div>

        {expanded ? <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0 mt-2" /> : <ChevronRight className="w-3.5 h-3.5 text-muted-foreground shrink-0 mt-2" />}
      </Button>

      {expanded && (
        <div className="px-3 pb-3 border-t border-border pt-3 ml-13">
          {/* Full Problem → Solution */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-3">
            <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-3">
              <div className="flex items-center gap-1.5 mb-2">
                <AlertTriangle className="w-3.5 h-3.5 text-red-500" />
                <span className="text-xs font-medium text-red-500">问题描述</span>
              </div>
              <p className="text-xs text-muted-foreground whitespace-pre-wrap">{problem || '无'}</p>
            </div>
            <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3">
              <div className="flex items-center gap-1.5 mb-2">
                <CheckCircle className="w-3.5 h-3.5 text-emerald-500" />
                <span className="text-xs font-medium text-emerald-500">解决方案</span>
              </div>
              <p className="text-xs text-muted-foreground whitespace-pre-wrap">{solution || '无'}</p>
            </div>
          </div>

          {/* Full content */}
          {parsed.detail !== problem && parsed.detail !== solution && (
            <div className="rounded-lg border border-border bg-muted/30 p-3 mb-3">
              <div className="text-[10px] text-muted-foreground mb-1">完整内容</div>
              <p className="text-xs text-muted-foreground whitespace-pre-wrap">{parsed.detail}</p>
            </div>
          )}

          {/* Metadata */}
          <div className="flex flex-wrap gap-2">
            {meta.session_id && (
              <Badge variant="muted" className="font-mono">
                会话: {meta.session_id}
              </Badge>
            )}
            {tags.map((tag) => (
              <Badge key={tag} variant="default">
                <Tag className="w-2.5 h-2.5 inline mr-0.5" />{tag}
              </Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────

export default function PatternsPage() {
  const [patterns, setPatterns] = useState<Pattern[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [confidenceFilter, setConfidenceFilter] = useState('all');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const pageSize = 20;

  const fetchPatterns = useCallback(async () => {
    try {
      setLoading(true);
      // Fetch from feedback API with type=pattern
      const data = await apiFetch(`/api/feedback/entries?type=pattern&limit=${pageSize}&offset=${page * pageSize}`);
      setPatterns(Array.isArray(data) ? data : []);
      setTotal(data.length || 0);
    } catch (e: any) {
      // Fallback: try knowledge API with content_type=pattern
      try {
        const data = await apiFetch(`/api/knowledge/?content_type=pattern&limit=${pageSize}&offset=${page * pageSize}`);
        if (Array.isArray(data)) {
          setPatterns(data.map((k: any) => ({
            id: k.id,
            title: k.title || '',
            content: k.content || '',
            metadata: k.metadata || {},
            created_at: k.created_at || '',
          })));
          setTotal(data.length);
        }
      } catch (e2: any) {
        setError(e2.message);
      }
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => { fetchPatterns(); }, [fetchPatterns]);

  // Filter
  const filtered = patterns.filter((p) => {
    const meta = p.metadata || {};
    if (confidenceFilter !== 'all' && meta.confidence !== confidenceFilter) return false;
    if (search) {
      const q = search.toLowerCase();
      return (p.title || '').toLowerCase().includes(q) ||
        (p.content || '').toLowerCase().includes(q) ||
        (meta.problem || '').toLowerCase().includes(q) ||
        (meta.solution || '').toLowerCase().includes(q) ||
        (meta.tags || []).some(t => t.toLowerCase().includes(q));
    }
    return true;
  });

  // Stats
  const highConf = patterns.filter(p => p.metadata?.confidence === 'high').length;
  const medConf = patterns.filter(p => p.metadata?.confidence === 'medium').length;
  const lowConf = patterns.filter(p => p.metadata?.confidence === 'low').length;

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{patterns.length}</div>
          <div className="text-xs text-muted-foreground">已发现模式</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-500">{highConf}</div>
          <div className="text-xs text-muted-foreground">高置信度</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-amber-500">{medConf}</div>
          <div className="text-xs text-muted-foreground">中置信度</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-red-500">{lowConf}</div>
          <div className="text-xs text-muted-foreground">低置信度</div>
        </div>
      </div>

      {/* Cumulative notice */}
      <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 flex items-center gap-2">
        <TrendingUp className="w-4 h-4 text-amber-500 shrink-0" />
        <div>
          <span className="text-xs font-medium text-amber-500">Wiki层 · 累积模式库</span>
          <p className="text-[10px] text-muted-foreground mt-0.5">
            模式只增不删。从执行轨迹中自动提取的问题→方案对，持续累积形成经验库。
          </p>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索问题、方案、标签..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={confidenceFilter}
          onChange={(e) => setConfidenceFilter(e.target.value)}
        >
          <option value="all">全部置信度</option>
          <option value="high">高置信</option>
          <option value="medium">中置信</option>
          <option value="low">低置信</option>
        </select>
        <Button onClick={() => fetchPatterns()} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
      </div>

      {/* Pattern list */}
      <div className="space-y-2">
        {filtered.map((pattern) => (
          <PatternCard
            key={pattern.id}
            pattern={pattern}
            expanded={expandedId === pattern.id}
            onToggle={() => setExpandedId(expandedId === pattern.id ? null : pattern.id)}
          />
        ))}
      </div>

      {/* Pagination */}
      {patterns.length >= pageSize && (
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted-foreground">第 {page + 1} 页</span>
          <div className="flex items-center gap-1">
            <Button onClick={() => setPage(Math.max(0, page - 1))}
              disabled={page === 0} variant="secondary" className="disabled:opacity-30">
              上一页
            </Button>
            <Button onClick={() => setPage(page + 1)}
              disabled={patterns.length < pageSize} variant="secondary" className="disabled:opacity-30">
              下一页
            </Button>
          </div>
        </div>
      )}

      {filtered.length === 0 && (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
          <Lightbulb className="w-10 h-10 mb-2 opacity-30" />
          <p className="text-xs">暂无发现的模式</p>
          <p className="text-[10px] mt-1">系统会从执行轨迹中自动识别重复的问题→方案对</p>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-red-500 shrink-0" />
          <span className="text-xs text-red-500">{error}</span>
          <Button onClick={() => setError('')} variant="icon-sm" className="ml-auto hover:bg-muted">
            <X className="w-3 h-3" />
          </Button>
        </div>
      )}
    </div>
  );
}
