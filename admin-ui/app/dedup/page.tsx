'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, RefreshCw, X, Copy, Trash2, AlertTriangle,
  CheckCircle, Loader2, Database, Layers, Eye, ArrowRight,
  Zap, Shield, BarChart3, Filter, ChevronDown, ChevronRight,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge, tone } from '@/components/ui/badge';

// ── Types ───────────────────────────────────────────────────

// CD-④ 元素键收编：DuplicatePair 收编真源嵌套形（get_duplicate_report 元素=
// {item_a:{id,title},item_b:{id,title},similarity}，deduplication.py 字面量真源）。
// 修前平铺 8 键形（平铺 id/title/内容类型/创建时间 四对键）对真源恒
// undefined：标题/ID 全空渲染、删除按钮 id 失效、搜索过滤对缺失 id 调
// toLowerCase 必炸（页崩）。
interface DuplicateItem {
  id: string;
  title: string;
}

interface DuplicatePair {
  item_a: DuplicateItem;
  item_b: DuplicateItem;
  similarity: number;
}

// CD-⑧ DedupResult 收编真源形（deduplicate_knowledge 返回字面量真源：
// {total, duplicates_found, duplicates_removed, removed_ids, details?}，
// details 元素=dict {id,title,duplicate_of,similarity} 非 string[]）。修前两处漂移：
// merged 键真源根本不产出=死显示键（「已合并」恒空白）、details 元素按 string 渲染
// {d} 对 dict 必炸 React child（去重完成即整页崩）。
interface DedupDetail {
  id: string;
  title: string | null;
  duplicate_of: string;
  similarity: number;
}

interface DedupResult {
  total: number;
  duplicates_found: number;
  duplicates_removed: number;
  removed_ids: string[];
  details?: DedupDetail[];
}

// ── Helpers ─────────────────────────────────────────────────

// （formatTime 已随 created_at_* 死显示面收口移除——真源元素无时间产出，
//  时间显示恢复=真源补现候选，见 pending-decisions ㉕。）

function SimilarityBar({ value }: { value: number }) {
  const pct = Math.min(100, Math.round(value * 100));
  const color = pct >= 90 ? 'bg-red-500' : pct >= 70 ? 'bg-amber-500' : 'bg-green-500';
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden w-20">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs font-mono w-10 text-right">{pct}%</span>
    </div>
  );
}

// ── Duplicate Pair Card ─────────────────────────────────────

function DuplicatePairCard({ pair, expanded, onToggle, onRemove }: {
  pair: DuplicatePair;
  expanded: boolean;
  onToggle: () => void;
  onRemove: (id: string) => void;
}) {
  const simPct = Math.round(pair.similarity * 100);
  const severity = simPct >= 90 ? 'high' : simPct >= 70 ? 'medium' : 'low';
  const severityConfig = {
    high: { color: 'border-red-500/30 bg-red-500/5', badge: tone('destructive'), label: '高度重复' },
    medium: { color: 'border-amber-500/30 bg-amber-500/5', badge: tone('warning'), label: '中度重复' },
    low: { color: 'border-emerald-500/30 bg-emerald-500/5', badge: tone('success'), label: '低度重复' },
  };
  const sev = severityConfig[severity];

  return (
    <div className={`border rounded-lg bg-card transition-colors ${sev.color}`}>
      <Button
        onClick={onToggle}
        variant="row" className="gap-3 p-3 hover:bg-muted/20 transition-colors"
      >
        <div className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${sev.badge}`}>
          <Copy className="w-5 h-5" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <Badge variant="bare" className={`${sev.badge}`}>{sev.label}</Badge>
          </div>
          <div className="mt-1 text-xs">
            <span className="text-foreground font-medium truncate">{pair.item_a.title || pair.item_a.id}</span>
            <ArrowRight className="w-3 h-3 inline mx-1 text-muted-foreground" />
            <span className="text-foreground font-medium truncate">{pair.item_b.title || pair.item_b.id}</span>
          </div>
        </div>
        <div className="shrink-0 mr-2">
          <SimilarityBar value={pair.similarity} />
        </div>
        {expanded ? <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 text-muted-foreground shrink-0" />}
      </Button>

      {expanded && (
        <div className="px-3 pb-3 border-t border-border pt-2 space-y-2">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="rounded-lg border border-border bg-muted/30 p-3">
              <div className="flex items-center gap-1.5 mb-1">
                <Database className="w-3 h-3 text-blue-500" />
                <span className="text-[10px] font-medium text-blue-500">条目 A</span>
              </div>
              <p className="text-xs font-medium">{pair.item_a.title || '无标题'}</p>
              <p className="text-[10px] text-muted-foreground font-mono mt-0.5">{pair.item_a.id}</p>
            </div>
            <div className="rounded-lg border border-border bg-muted/30 p-3">
              <div className="flex items-center gap-1.5 mb-1">
                <Database className="w-3 h-3 text-violet-500" />
                <span className="text-[10px] font-medium text-violet-500">条目 B</span>
              </div>
              <p className="text-xs font-medium">{pair.item_b.title || '无标题'}</p>
              <p className="text-[10px] text-muted-foreground font-mono mt-0.5">{pair.item_b.id}</p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Button
              onClick={() => onRemove(pair.item_b.id)}
              variant="chip-sm" className="flex items-center gap-1.5 bg-red-500/10 text-red-500 border border-red-500/30 hover:bg-red-500/20"
            >
              <Trash2 className="w-3 h-3" />删除条目B（保留A）
            </Button>
            <Button
              onClick={() => onRemove(pair.item_a.id)}
              variant="chip-sm" className="flex items-center gap-1.5 bg-red-500/10 text-red-500 border border-red-500/30 hover:bg-red-500/20"
            >
              <Trash2 className="w-3 h-3" />删除条目A（保留B）
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────

export default function DedupPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [duplicates, setDuplicates] = useState<DuplicatePair[]>([]);
  const [totalPairs, setTotalPairs] = useState(0);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [simFilter, setSimFilter] = useState('all');
  const [deduping, setDeduping] = useState(false);
  const [dedupResult, setDedupResult] = useState<DedupResult | null>(null);
  const [showDedupConfirm, setShowDedupConfirm] = useState(false);

  const fetchDuplicates = useCallback(async () => {
    try {
      setLoading(true);
      setError('');
      const data = await apiFetch('/api/dedup/duplicates');
      setDuplicates(data.duplicates || []);
      setTotalPairs(data.total_pairs || 0);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchDuplicates(); }, [fetchDuplicates]);

  const handleDeduplicate = async () => {
    setDeduping(true);
    setDedupResult(null);
    try {
      const data = await apiFetch('/api/dedup/deduplicate', { method: 'POST' });
      setDedupResult(data);
      // Refresh list after dedup
      fetchDuplicates();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setDeduping(false);
      setShowDedupConfirm(false);
    }
  };

  const handleRemove = (id: string) => {
    // Just remove from local state (actual deletion would need a dedicated API)
    setDuplicates((prev) => prev.filter((p) => p.item_a.id !== id && p.item_b.id !== id));
    setTotalPairs((prev) => Math.max(0, prev - 1));
  };

  // Filter
  const filtered = duplicates.filter((p) => {
    if (simFilter === 'high' && p.similarity < 0.9) return false;
    if (simFilter === 'medium' && (p.similarity < 0.7 || p.similarity >= 0.9)) return false;
    if (simFilter === 'low' && p.similarity >= 0.7) return false;
    if (search) {
      const q = search.toLowerCase();
      // 收编真源键（修前平铺 id 键恒 undefined 调 toLowerCase 必炸=搜索即页崩；
      // content_type 真源不产出，过滤面一并收口禁搜不存在的字段）
      return (p.item_a.title || '').toLowerCase().includes(q) ||
        (p.item_b.title || '').toLowerCase().includes(q) ||
        (p.item_a.id || '').toLowerCase().includes(q) ||
        (p.item_b.id || '').toLowerCase().includes(q);
    }
    return true;
  });

  // Stats
  const highSim = duplicates.filter((p) => p.similarity >= 0.9).length;
  const medSim = duplicates.filter((p) => p.similarity >= 0.7 && p.similarity < 0.9).length;
  const lowSim = duplicates.filter((p) => p.similarity < 0.7).length;

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* ── Stats ── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{totalPairs}</div>
          <div className="text-xs text-muted-foreground">重复对数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-red-500">{highSim}</div>
          <div className="text-xs text-muted-foreground">高度重复（≥90%）</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-amber-500">{medSim}</div>
          <div className="text-xs text-muted-foreground">中度重复（70-90%）</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-emerald-500">{lowSim}</div>
          <div className="text-xs text-muted-foreground">低度重复（&lt;70%）</div>
        </div>
      </div>

      {/* ── Toolbar ── */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索标题、ID..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={simFilter}
          onChange={(e) => setSimFilter(e.target.value)}
        >
          <option value="all">全部相似度</option>
          <option value="high">≥90% 高度</option>
          <option value="medium">70-90% 中度</option>
          <option value="low">&lt;70% 低度</option>
        </select>
        <Button onClick={fetchDuplicates} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
        <Button onClick={() => setShowDedupConfirm(true)}
          disabled={deduping || duplicates.length === 0} variant="primary" className="gap-1.5">
          {deduping ? <Loader2 className="w-3 h-3 animate-spin" /> : <Zap className="w-3 h-3" />}
          自动去重
        </Button>
      </div>

      {/* ── Dedup Result ── */}
      {dedupResult && (
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-4">
          <div className="flex items-center gap-2 mb-2">
            <CheckCircle className="w-4 h-4 text-emerald-500" />
            <span className="text-sm font-medium text-emerald-500">去重完成</span>
          </div>
          <div className="grid grid-cols-3 gap-3 text-center">
            <div>
              <div className="text-lg font-bold">{dedupResult.duplicates_found}</div>
              <div className="text-[10px] text-muted-foreground">发现重复</div>
            </div>
            <div>
              <div className="text-lg font-bold text-emerald-500">{dedupResult.duplicates_removed}</div>
              <div className="text-[10px] text-muted-foreground">已删除</div>
            </div>
            <div>
              <div className="text-lg font-bold text-blue-500">{dedupResult.total}</div>
              <div className="text-[10px] text-muted-foreground">扫描条目</div>
            </div>
          </div>
          {dedupResult.details && dedupResult.details.length > 0 && (
            <div className="mt-2 space-y-0.5">
              {dedupResult.details.slice(0, 5).map((d, i) => (
                <p key={i} className="text-[10px] text-muted-foreground">
                  已删除 {d.title || d.id}（与 {d.duplicate_of} 重复，相似度 {Math.round(d.similarity * 100)}%）
                </p>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Info Banner ── */}
      <div className="rounded-lg border border-blue-500/30 bg-blue-500/5 p-3 flex items-center gap-2">
        <Shield className="w-4 h-4 text-blue-500 shrink-0" />
        <div>
          <span className="text-xs font-medium text-blue-500">知识去重引擎</span>
          <p className="text-[10px] text-muted-foreground mt-0.5">
            自动检测知识库中的重复条目。"自动去重"会删除重复项并保留最新版本。手动检查可逐对审查。
          </p>
        </div>
      </div>

      {/* ── Duplicate List ── */}
      <div className="space-y-2">
        {filtered.map((pair) => {
          const pairKey = `${pair.item_a.id}-${pair.item_b.id}`;
          return (
            <DuplicatePairCard
              key={pairKey}
              pair={pair}
              expanded={expandedId === pairKey}
              onToggle={() => setExpandedId(expandedId === pairKey ? null : pairKey)}
              onRemove={handleRemove}
            />
          );
        })}
      </div>

      {filtered.length === 0 && (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
          <CheckCircle className="w-10 h-10 mb-2 opacity-30 text-emerald-500" />
          <p className="text-xs">
            {duplicates.length === 0 ? '知识库无重复条目' : '无匹配的重复项'}
          </p>
          <p className="text-[10px] mt-1">
            {duplicates.length === 0 ? '所有知识条目都是唯一的' : '尝试调整筛选条件'}
          </p>
        </div>
      )}

      {/* ── Confirm Dialog ── */}
      {showDedupConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowDedupConfirm(false)}>
          <div className="w-full max-w-sm rounded-xl border border-border bg-card p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center gap-2 mb-3">
              <AlertTriangle className="w-5 h-5 text-amber-500" />
              <span className="text-sm font-medium">确认自动去重</span>
            </div>
            <p className="text-xs text-muted-foreground mb-4">
              将自动删除 {totalPairs} 组重复条目中的冗余项，保留最新版本。此操作不可撤销。
            </p>
            <div className="flex justify-end gap-2">
              <Button onClick={() => setShowDedupConfirm(false)} variant="secondary">
                取消
              </Button>
              <Button
                onClick={handleDeduplicate}
                disabled={deduping}
                variant="chip-sm" className="flex items-center gap-1.5 bg-red-500 text-white hover:bg-red-600 disabled:opacity-50"
              >
                {deduping ? <Loader2 className="w-3 h-3 animate-spin" /> : <Zap className="w-3 h-3" />}
                确认去重
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* ── Error ── */}
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
