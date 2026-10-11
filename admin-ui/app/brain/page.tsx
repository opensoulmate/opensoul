'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Brain, BookOpen, Lightbulb, GitBranch, MessageSquare, Search,
  RefreshCw, Filter, Eye, Tag, TrendingUp, Database, Network, X,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '../../components/ui/button';

// ── Types ───────────────────────────────────────────────────────

interface KnowledgeEntry {
  id: string;
  title: string;
  content: string;
  content_type: string;
  source: string;
  tags: string[];
  starred: boolean;
  pinned: boolean;
  created_at: string;
  updated_at: string;
}

interface KnowledgeStats {
  total_entries: number;
  recent_24h: number;
  top_users: Array<{ user_id: string; count: number }>;
}

interface GraphStats {
  total_entities: number;
  total_relations: number;
  by_type: Record<string, number>;
}

// ── Content type config ─────────────────────────────────────────

const TYPE_CONFIG: Record<string, { label: string; icon: typeof BookOpen; color: string; bg: string }> = {
  knowledge: { label: '知识', icon: BookOpen, color: 'text-blue-400', bg: 'bg-blue-500/10' },
  pattern: { label: '模式', icon: Lightbulb, color: 'text-amber-400', bg: 'bg-amber-500/10' },
  decision: { label: '决策', icon: GitBranch, color: 'text-emerald-400', bg: 'bg-emerald-500/10' },
  feedback: { label: '反馈', icon: MessageSquare, color: 'text-purple-400', bg: 'bg-purple-500/10' },
  text: { label: '文本', icon: BookOpen, color: 'text-slate-400', bg: 'bg-slate-500/10' },
};

function getTypeConfig(type: string) {
  return TYPE_CONFIG[type] || { label: type || '未知', icon: Database, color: 'text-gray-400', bg: 'bg-gray-500/10' };
}

// ── Force Graph ─────────────────────────────────────────────────

interface LayoutNode {
  id: string;
  label: string;
  type: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  radius: number;
}

function KnowledgeGraph({
  entries,
  selectedType,
  onNodeClick,
}: {
  entries: KnowledgeEntry[];
  selectedType: string;
  onNodeClick: (entry: KnowledgeEntry) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const nodesRef = useRef<LayoutNode[]>([]);
  const animRef = useRef<number>(0);
  const filtered = selectedType === 'all' ? entries : entries.filter((e) => e.content_type === selectedType);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const w = canvas.parentElement?.clientWidth || 800;
    const h = 500;
    canvas.width = w * 2;
    canvas.height = h * 2;
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    ctx.scale(2, 2);

    // Build nodes from entries
    const nodes: LayoutNode[] = filtered.slice(0, 60).map((e, i) => ({
      id: e.id,
      label: e.title.slice(0, 12),
      type: e.content_type || 'text',
      x: w / 2 + (Math.random() - 0.5) * w * 0.6,
      y: h / 2 + (Math.random() - 0.5) * h * 0.6,
      vx: 0,
      vy: 0,
      radius: e.pinned ? 18 : e.starred ? 14 : 10,
    }));
    nodesRef.current = nodes;

    // Build edges from shared tags
    const edges: Array<[number, number]> = [];
    const tagMap = new Map<string, number[]>();
    filtered.slice(0, 60).forEach((e, i) => {
      (e.tags || []).forEach((t) => {
        const key = t.toLowerCase();
        if (!tagMap.has(key)) tagMap.set(key, []);
        tagMap.get(key)!.push(i);
      });
    });
    tagMap.forEach((indices) => {
      for (let i = 0; i < indices.length; i++) {
        for (let j = i + 1; j < indices.length; j++) {
          edges.push([indices[i], indices[j]]);
        }
      }
    });

    let running = true;
    function tick() {
      if (!running || !ctx) return;
      // Simple force simulation
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const dx = nodes[j].x - nodes[i].x;
          const dy = nodes[j].y - nodes[i].y;
          const dist = Math.sqrt(dx * dx + dy * dy) || 1;
          const force = 800 / (dist * dist);
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;
          nodes[i].vx -= fx;
          nodes[i].vy -= fy;
          nodes[j].vx += fx;
          nodes[j].vy += fy;
        }
      }
      // Attract edges
      for (const [a, b] of edges) {
        const dx = nodes[b].x - nodes[a].x;
        const dy = nodes[b].y - nodes[a].y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const force = (dist - 80) * 0.005;
        nodes[a].vx += (dx / dist) * force;
        nodes[a].vy += (dy / dist) * force;
        nodes[b].vx -= (dx / dist) * force;
        nodes[b].vy -= (dy / dist) * force;
      }
      // Center gravity
      for (const n of nodes) {
        n.vx += (w / 2 - n.x) * 0.001;
        n.vy += (h / 2 - n.y) * 0.001;
        n.vx *= 0.85;
        n.vy *= 0.85;
        n.x += n.vx;
        n.y += n.vy;
        n.x = Math.max(20, Math.min(w - 20, n.x));
        n.y = Math.max(20, Math.min(h - 20, n.y));
      }
      // Draw
      ctx.clearRect(0, 0, w, h);
      ctx.strokeStyle = 'rgba(100,100,140,0.15)';
      ctx.lineWidth = 1;
      for (const [a, b] of edges) {
        ctx.beginPath();
        ctx.moveTo(nodes[a].x, nodes[a].y);
        ctx.lineTo(nodes[b].x, nodes[b].y);
        ctx.stroke();
      }
      for (const n of nodes) {
        const cfg = getTypeConfig(n.type);
        const hex = cfg.color.replace('text-', '').replace('-400', '');
        const colorMap: Record<string, string> = {
          blue: '#60a5fa', amber: '#fbbf24', emerald: '#34d399',
          purple: '#a78bfa', slate: '#94a3b8', gray: '#9ca3af',
        };
        const fill = colorMap[hex] || '#6b7280';
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.radius, 0, Math.PI * 2);
        ctx.fillStyle = fill + '33';
        ctx.fill();
        ctx.strokeStyle = fill;
        ctx.lineWidth = 1.5;
        ctx.stroke();
        ctx.fillStyle = '#e2e8f0';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(n.label, n.x, n.y + n.radius + 12);
      }
      animRef.current = requestAnimationFrame(tick);
    }
    tick();
    return () => { running = false; cancelAnimationFrame(animRef.current); };
  }, [filtered]);

  return (
    <canvas
      ref={canvasRef}
      className="w-full rounded-lg border border-border bg-card/50 cursor-crosshair"
      style={{ height: 500 }}
    />
  );
}

// ── Main Page ───────────────────────────────────────────────────

export default function BrainPage() {
  const [entries, setEntries] = useState<KnowledgeEntry[]>([]);
  const [kStats, setKStats] = useState<KnowledgeStats | null>(null);
  const [gStats, setGStats] = useState<GraphStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [filterType, setFilterType] = useState('all');
  const [selectedEntry, setSelectedEntry] = useState<KnowledgeEntry | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [kData, kStatsData, gStatsData] = await Promise.all([
        apiFetch('/api/knowledge/?limit=200').catch(() => []),
        apiFetch('/api/knowledge/stats').catch(() => null),
        apiFetch('/api/graph/stats').catch(() => null),
      ]);
      const list = Array.isArray(kData) ? kData : (kData as { entries?: KnowledgeEntry[] }).entries || [];
      setEntries(list);
      setKStats(kStatsData as KnowledgeStats);
      setGStats(gStatsData as GraphStats);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Type distribution
  const typeDistribution = entries.reduce<Record<string, number>>((acc, e) => {
    const t = e.content_type || 'text';
    acc[t] = (acc[t] || 0) + 1;
    return acc;
  }, {});
  const sortedTypes = Object.entries(typeDistribution).sort((a, b) => b[1] - a[1]);
  const maxCount = sortedTypes.length > 0 ? sortedTypes[0][1] : 1;

  // Tag frequency
  const tagFreq = entries.reduce<Record<string, number>>((acc, e) => {
    (e.tags || []).forEach((t) => { acc[t] = (acc[t] || 0) + 1; });
    return acc;
  }, {});
  const topTags = Object.entries(tagFreq).sort((a, b) => b[1] - a[1]).slice(0, 20);

  // Filter
  const filtered = entries.filter((e) => {
    const matchType = filterType === 'all' || e.content_type === filterType;
    const matchSearch = !search || e.title.toLowerCase().includes(search.toLowerCase()) || e.content.toLowerCase().includes(search.toLowerCase());
    return matchType && matchSearch;
  });

  if (loading) {
    return <div className="flex items-center justify-center h-64"><div className="text-sm text-muted-foreground">加载中...</div></div>;
  }

  return (
    <div className="space-y-6">
      {/* Stats cards */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-center gap-2 text-muted-foreground text-xs mb-2">
            <Brain className="w-3.5 h-3.5" />知识总量
          </div>
          <div className="text-2xl font-bold">{kStats?.total_entries ?? entries.length}</div>
          <div className="text-xs text-muted-foreground mt-1">条目</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-center gap-2 text-muted-foreground text-xs mb-2">
            <TrendingUp className="w-3.5 h-3.5" />近24h新增
          </div>
          <div className="text-2xl font-bold">{kStats?.recent_24h ?? 0}</div>
          <div className="text-xs text-muted-foreground mt-1">条目</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-center gap-2 text-muted-foreground text-xs mb-2">
            <Network className="w-3.5 h-3.5" />实体
          </div>
          <div className="text-2xl font-bold">{gStats?.total_entities ?? 0}</div>
          <div className="text-xs text-muted-foreground mt-1">节点</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-center gap-2 text-muted-foreground text-xs mb-2">
            <Network className="w-3.5 h-3.5" />关系
          </div>
          <div className="text-2xl font-bold">{gStats?.total_relations ?? 0}</div>
          <div className="text-xs text-muted-foreground mt-1">连接</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-center gap-2 text-muted-foreground text-xs mb-2">
            <Tag className="w-3.5 h-3.5" />标签
          </div>
          <div className="text-2xl font-bold">{Object.keys(tagFreq).length}</div>
          <div className="text-xs text-muted-foreground mt-1">种</div>
        </div>
      </div>

      {/* Type distribution + Tag cloud */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Type distribution */}
        <div className="rounded-lg border border-border bg-card p-4">
          <h3 className="text-sm font-medium mb-4">知识类型分布</h3>
          <div className="space-y-3">
            {sortedTypes.map(([type, count]) => {
              const cfg = getTypeConfig(type);
              const Icon = cfg.icon;
              const pct = Math.round((count / maxCount) * 100);
              const barColor = cfg.color.includes('blue') ? '#60a5fa' : cfg.color.includes('amber') ? '#fbbf24' : cfg.color.includes('emerald') ? '#34d399' : cfg.color.includes('purple') ? '#a78bfa' : cfg.color.includes('slate') ? '#94a3b8' : '#9ca3af';
              return (
                <Button variant="bare"
                  key={type}
                  onClick={() => setFilterType(filterType === type ? 'all' : type)}
                  className={`w-full text-left group transition-colors rounded-md p-2 -m-2 ${filterType === type ? 'bg-primary/10' : 'hover:bg-muted/50'}`}
                >
                  <div className="flex items-center gap-2 mb-1.5">
                    <Icon className={`w-3.5 h-3.5 ${cfg.color}`} />
                    <span className="text-sm font-medium">{cfg.label}</span>
                    <span className="text-xs text-muted-foreground ml-auto">{count}</span>
                  </div>
                  <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-500`}
                      style={{ width: `${pct}%`, backgroundColor: barColor }}
                    />
                  </div>
                </Button>
              );
            })}
          </div>
        </div>

        {/* Tag cloud */}
        <div className="rounded-lg border border-border bg-card p-4">
          <h3 className="text-sm font-medium mb-4">热门标签</h3>
          {topTags.length === 0 ? (
            <div className="text-sm text-muted-foreground text-center py-8">暂无标签数据</div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {topTags.map(([tag, count]) => {
                const size = Math.max(12, Math.min(20, 12 + count * 2));
                return (
                  <Button variant="bare"
                    key={tag}
                    onClick={() => setSearch(tag)}
                    className="inline-flex items-center gap-1 rounded-full border border-border px-2.5 py-1 hover:bg-muted/50 transition-colors"
                    style={{ fontSize: size }}
                  >
                    <span className="text-muted-foreground">{tag}</span>
                    <span className="text-xs text-muted-foreground/60">{count}</span>
                  </Button>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Knowledge network graph */}
      <div className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-medium">知识关联网络</h3>
          <div className="flex items-center gap-2">
            {sortedTypes.map(([type]) => {
              const cfg = getTypeConfig(type);
              return (
                <Button variant="bare"
                  key={type}
                  onClick={() => setFilterType(filterType === type ? 'all' : type)}
                  className={`text-xs lg:text-sm px-2 py-1 rounded-full border transition-colors ${filterType === type ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground hover:text-foreground'}`}
                >
                  {cfg.label}
                </Button>
              );
            })}
          </div>
        </div>
        {entries.length === 0 ? (
          <div className="flex items-center justify-center h-64 text-sm text-muted-foreground">暂无知识数据</div>
        ) : (
          <KnowledgeGraph entries={entries} selectedType={filterType} onNodeClick={setSelectedEntry} />
        )}
      </div>

      {/* Search + Entry list */}
      <div className="rounded-lg border border-border bg-card">
        <div className="p-4 border-b border-border flex items-center gap-3 flex-wrap">
          <div className="relative flex-1 min-w-[200px]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索知识条目..."
              className="w-full pl-9 pr-8 py-2 rounded-md bg-muted border border-border text-sm focus:outline-none focus:ring-1 focus:ring-primary"
            />
            {search && (
              <Button variant="bare" onClick={() => setSearch('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
                <X className="w-4 h-4" />
              </Button>
            )}
          </div>
          <Button variant="bare" onClick={fetchData} className="flex items-center gap-1.5 text-xs lg:text-sm text-muted-foreground hover:text-foreground transition-colors px-3 py-2 rounded-lg border border-border hover:bg-muted/50">
            <RefreshCw className="w-3.5 h-3.5" />刷新
          </Button>
        </div>

        {/* Entry list grouped by type */}
        <div className="divide-y divide-border">
          {filtered.length === 0 ? (
            <div className="p-8 text-center text-sm text-muted-foreground">无匹配条目</div>
          ) : (
            filtered.map((entry) => {
              const cfg = getTypeConfig(entry.content_type);
              const Icon = cfg.icon;
              return (
                <Button variant="bare"
                  key={entry.id}
                  onClick={() => setSelectedEntry(entry)}
                  className="w-full text-left p-4 hover:bg-muted/30 transition-colors"
                >
                  <div className="flex items-start gap-3">
                    <div className={`mt-0.5 p-1.5 rounded-md ${cfg.bg}`}>
                      <Icon className={`w-4 h-4 ${cfg.color}`} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-sm font-medium truncate">{entry.title}</span>
                        {entry.pinned && <Badge variant="default">置顶</Badge>}
                        {entry.starred && <Badge variant="warning" className="text-amber-400">收藏</Badge>}
                      </div>
                      <p className="text-xs text-muted-foreground line-clamp-2">{entry.content.slice(0, 120)}</p>
                      <div className="flex items-center gap-2 mt-1.5">
                        <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${cfg.bg} ${cfg.color}`}>{cfg.label}</span>
                        {entry.source && <span className="text-[10px] text-muted-foreground">{entry.source}</span>}
                        {(entry.tags || []).slice(0, 3).map((t) => (
                          <Badge variant="muted" key={t}>{t}</Badge>
                        ))}
                        <span className="text-[10px] text-muted-foreground ml-auto">{new Date(entry.created_at).toLocaleDateString('zh-CN')}</span>
                      </div>
                    </div>
                  </div>
                </Button>
              );
            })
          )}
        </div>
      </div>

      {/* Detail modal */}
      {selectedEntry && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={() => setSelectedEntry(null)}>
          <div className="bg-card border border-border rounded-xl max-w-2xl w-full max-h-[80vh] overflow-auto p-6" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">{selectedEntry.title}</h2>
              <Button variant="bare" onClick={() => setSelectedEntry(null)} className="text-muted-foreground hover:text-foreground"><X className="w-5 h-5" /></Button>
            </div>
            <div className="flex items-center gap-2 mb-4">
              {(() => { const cfg = getTypeConfig(selectedEntry.content_type); const Icon = cfg.icon; return <span className={`inline-flex items-center gap-1 text-xs px-2 py-1 rounded-full ${cfg.bg} ${cfg.color}`}><Icon className="w-3 h-3" />{cfg.label}</span>; })()}
              {selectedEntry.source && <span className="text-xs text-muted-foreground">来源: {selectedEntry.source}</span>}
            </div>
            <div className="text-sm text-foreground/80 whitespace-pre-wrap mb-4">{selectedEntry.content}</div>
            {(selectedEntry.tags || []).length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {selectedEntry.tags.map((t) => <Badge key={t} variant="muted">{t}</Badge>)}
              </div>
            )}
            <div className="text-xs text-muted-foreground mt-4 pt-4 border-t border-border">
              创建: {new Date(selectedEntry.created_at).toLocaleString('zh-CN')} | 更新: {new Date(selectedEntry.updated_at).toLocaleString('zh-CN')}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
