'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Package, RefreshCw, X, ChevronRight, ChevronDown,
  Layers, GitBranch, Zap, Filter, BarChart3, ExternalLink,
  CheckCircle, AlertCircle, Loader2
} from 'lucide-react';
import { Tab, TabBar } from '@/components/ui/tab';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

interface Component {
  id: string;
  name: string;
  emoji: string;
  category: string;
  layer: string;
  description: string;
  api_prefix: string;
  health_endpoint: string;
  capabilities: string[];
  dependencies: string[];
  version: string;
  phase: string;
  dependents?: string[];
}

interface RegistryGraph {
  nodes: { id: string; name: string; emoji: string; category: string; layer: string; phase: string }[];
  edges: { from: string; to: string }[];
  layers: string[][];
  stats: {
    total_components: number;
    total_dependencies: number;
    categories: Record<string, number>;
    phases: Record<string, number>;
  };
}

interface CapabilityMap {
  total_capabilities: number;
  capabilities: Record<string, string[]>;
}

const CATEGORY_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  core: { bg: 'bg-blue-500/10', text: 'text-blue-600', border: 'border-blue-500/20' },
  platform: { bg: 'bg-green-500/10', text: 'text-green-600', border: 'border-green-500/20' },
  advanced: { bg: 'bg-purple-500/10', text: 'text-purple-600', border: 'border-purple-500/20' },
  system: { bg: 'bg-orange-500/10', text: 'text-orange-600', border: 'border-orange-500/20' },
};

const PHASE_COLORS: Record<string, string> = {
  '一期': 'bg-emerald-500/10 text-emerald-500',
  '二期': 'bg-blue-500/10 text-blue-600',
  '三期': 'bg-orange-500/10 text-orange-600',
  '四期': 'bg-purple-500/10 text-purple-600',
};

export default function RegistryPage() {
  const [components, setComponents] = useState<Component[]>([]);
  const [graph, setGraph] = useState<RegistryGraph | null>(null);
  const [capabilities, setCapabilities] = useState<CapabilityMap | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [phaseFilter, setPhaseFilter] = useState('');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [view, setView] = useState<'grid' | 'graph' | 'capabilities'>('grid');
  const [healthChecks, setHealthChecks] = useState<Record<string, boolean>>({});
  const [checkingHealth, setCheckingHealth] = useState(false);

  const fetchAll = useCallback(async () => {
    try {
      setLoading(true);
      const [compData, graphData, capData] = await Promise.all([
        apiFetch('/api/registry/components'),
        apiFetch('/api/registry/graph'),
        apiFetch('/api/registry/capabilities'),
      ]);
      setComponents(Array.isArray(compData.components) ? compData.components : []);
      setGraph(graphData);
      setCapabilities(capData);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const handleHealthCheck = async () => {
    setCheckingHealth(true);
    const results: Record<string, boolean> = {};
    for (const comp of components) {
      try {
        const res = await fetch(comp.health_endpoint, { signal: AbortSignal.timeout(5000) });
        results[comp.id] = res.ok;
      } catch {
        results[comp.id] = false;
      }
    }
    setHealthChecks(results);
    setCheckingHealth(false);
  };

  const handleSearchAPI = async (q: string) => {
    if (!q.trim()) { fetchAll(); return; }
    try {
      setLoading(true);
      const data = await apiFetch(`/api/registry/search?q=${encodeURIComponent(q)}`);
      setComponents(Array.isArray(data.results) ? data.results : []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const filtered = components.filter((c) => {
    if (categoryFilter && c.category !== categoryFilter) return false;
    if (phaseFilter && c.phase !== phaseFilter) return false;
    if (search && !c.name.toLowerCase().includes(search.toLowerCase()) &&
        !c.description.toLowerCase().includes(search.toLowerCase()) &&
        !c.id.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  const categories = Array.from(new Set(components.map((c) => c.category))).sort();
  const phases = Array.from(new Set(components.map((c) => c.phase))).sort();

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <Package className="w-4 h-4 text-primary" />
            <span className="text-xs text-muted-foreground">组件总数</span>
          </div>
          <div className="text-2xl font-bold mt-1">{graph?.stats?.total_components || components.length}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <GitBranch className="w-4 h-4 text-blue-500" />
            <span className="text-xs text-muted-foreground">依赖关系</span>
          </div>
          <div className="text-2xl font-bold mt-1">{graph?.stats?.total_dependencies || 0}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <Zap className="w-4 h-4 text-orange-500" />
            <span className="text-xs text-muted-foreground">能力总数</span>
          </div>
          <div className="text-2xl font-bold mt-1">{capabilities?.total_capabilities || 0}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <Layers className="w-4 h-4 text-green-500" />
            <span className="text-xs text-muted-foreground">架构层级</span>
          </div>
          <div className="text-2xl font-bold mt-1">{graph?.layers?.length || 0}</div>
        </div>
      </div>

      {/* View tabs + Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <TabBar>
          {(['grid', 'graph', 'capabilities'] as const).map((v) => (
            <Tab active={view === v} key={v} onClick={() => setView(v)}>
              {v === 'grid' ? '列表' : v === 'graph' ? '依赖图' : '能力'}
            </Tab>
          ))}
        </TabBar>
        <div className="relative flex-1 min-w-[180px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索组件..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSearchAPI(search)}
          />
        </div>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={categoryFilter}
          onChange={(e) => setCategoryFilter(e.target.value)}
        >
          <option value="">全部类别</option>
          {categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={phaseFilter}
          onChange={(e) => setPhaseFilter(e.target.value)}
        >
          <option value="">全部阶段</option>
          {phases.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <Button onClick={handleHealthCheck} disabled={checkingHealth} variant="secondary">
          {checkingHealth ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle className="w-3.5 h-3.5" />}
          健康检查
        </Button>
        <Button onClick={fetchAll} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 p-2 text-xs text-red-500 bg-red-500/5 rounded-md border border-red-500/20">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <Button variant="bare" onClick={() => setError('')}><X className="w-3 h-3" /></Button>
        </div>
      )}

      {/* Grid View */}
      {view === 'grid' && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
          {filtered.map((comp) => {
            const catColor = CATEGORY_COLORS[comp.category] || CATEGORY_COLORS.core;
            const isExpanded = expandedId === comp.id;
            const health = healthChecks[comp.id];
            return (
              <div
                key={comp.id}
                className={`rounded-lg border p-3 transition-all cursor-pointer ${catColor.border} bg-card hover:bg-muted/30`}
                onClick={() => setExpandedId(isExpanded ? null : comp.id)}
              >
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-xl">{comp.emoji}</span>
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5">
                        <h4 className="text-xs font-medium truncate">{comp.name}</h4>
                        {health !== undefined && (
                          health ?
                            <CheckCircle className="w-3 h-3 text-green-500 shrink-0" /> :
                            <AlertCircle className="w-3 h-3 text-red-500 shrink-0" />
                        )}
                      </div>
                      <div className="flex items-center gap-1 mt-0.5">
                        <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${catColor.bg} ${catColor.text}`}>{comp.category}</span>
                        <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${PHASE_COLORS[comp.phase] || 'bg-muted text-muted-foreground'}`}>{comp.phase}</span>
                      </div>
                    </div>
                  </div>
                  {isExpanded ? <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 text-muted-foreground shrink-0" />}
                </div>
                <p className="text-[11px] text-muted-foreground mt-1.5 line-clamp-2">{comp.description}</p>
                <div className="text-[10px] text-muted-foreground mt-1">层: {comp.layer}</div>

                {isExpanded && (
                  <div className="mt-3 pt-2 border-t border-border space-y-2">
                    <div>
                      <span className="text-[10px] font-medium text-muted-foreground">API前缀</span>
                      <Badge variant="muted" className="font-mono mt-0.5">{comp.api_prefix}</Badge>
                    </div>
                    <div>
                      <span className="text-[10px] font-medium text-muted-foreground">能力</span>
                      <div className="flex flex-wrap gap-1 mt-0.5">
                        {comp.capabilities?.map((cap) => (
                          <Badge variant="default" key={cap}>{cap}</Badge>
                        ))}
                      </div>
                    </div>
                    {comp.dependencies?.length > 0 && (
                      <div>
                        <span className="text-[10px] font-medium text-muted-foreground">依赖</span>
                        <div className="flex flex-wrap gap-1 mt-0.5">
                          {comp.dependencies.map((dep) => (
                            <Badge variant="warning" key={dep} className="bg-orange-500/10 text-orange-600">{dep}</Badge>
                          ))}
                        </div>
                      </div>
                    )}
                    {comp.dependents && comp.dependents.length > 0 && (
                      <div>
                        <span className="text-[10px] font-medium text-muted-foreground">被依赖</span>
                        <div className="flex flex-wrap gap-1 mt-0.5">
                          {comp.dependents.map((dep) => (
                            <Badge variant="info" key={dep} className="text-blue-600">{dep}</Badge>
                          ))}
                        </div>
                      </div>
                    )}
                    <div className="text-[10px] text-muted-foreground">版本: {comp.version}</div>
                  </div>
                )}
              </div>
            );
          })}
          {filtered.length === 0 && (
            <div className="col-span-full flex flex-col items-center justify-center py-12 text-muted-foreground">
              <Package className="w-10 h-10 mb-2 opacity-30" />
              <p className="text-xs">没有匹配的组件</p>
            </div>
          )}
        </div>
      )}

      {/* Graph View */}
      {view === 'graph' && graph && (
        <div className="space-y-3">
          {/* Layer diagram */}
          <div className="rounded-lg border border-border bg-card p-4">
            <h3 className="text-xs font-medium mb-3">依赖层级 (拓扑排序)</h3>
            <div className="space-y-2">
              {graph.layers.map((layer, idx) => (
                <div key={idx} className="flex items-center gap-2">
                  <span className="text-[10px] text-muted-foreground w-12 shrink-0">Layer {idx}</span>
                  <div className="flex flex-wrap gap-1.5 flex-1">
                    {layer.map((nodeId) => {
                      const node = graph.nodes.find((n) => n.id === nodeId);
                      if (!node) return null;
                      const catColor = CATEGORY_COLORS[node.category] || CATEGORY_COLORS.core;
                      return (
                        <span
                          key={nodeId}
                          className={`text-[10px] px-2 py-1 rounded-md border ${catColor.border} ${catColor.bg} ${catColor.text} flex items-center gap-1`}
                        >
                          <span>{node.emoji}</span>
                          <span>{node.name}</span>
                        </span>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Edge list */}
          <div className="rounded-lg border border-border bg-card p-4">
            <h3 className="text-xs font-medium mb-3">依赖关系 ({graph.edges.length})</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-1 max-h-[400px] overflow-auto">
              {graph.edges.map((edge, idx) => {
                const fromNode = graph.nodes.find((n) => n.id === edge.from);
                const toNode = graph.nodes.find((n) => n.id === edge.to);
                return (
                  <div key={idx} className="flex items-center gap-1.5 text-[11px] py-0.5">
                    <span>{fromNode?.emoji}</span>
                    <span className="font-medium">{fromNode?.name || edge.from}</span>
                    <ChevronRight className="w-3 h-3 text-muted-foreground" />
                    <span>{toNode?.emoji}</span>
                    <span className="font-medium">{toNode?.name || edge.to}</span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Category/Phase stats */}
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-lg border border-border bg-card p-4">
              <h3 className="text-xs font-medium mb-2">按类别</h3>
              <div className="space-y-1.5">
                {Object.entries(graph.stats.categories).map(([cat, count]) => {
                  const catColor = CATEGORY_COLORS[cat] || CATEGORY_COLORS.core;
                  return (
                    <div key={cat} className="flex items-center justify-between">
                      <Badge className={`${catColor.bg} ${catColor.text}`}>{cat}</Badge>
                      <span className="text-xs font-medium">{count}</span>
                    </div>
                  );
                })}
              </div>
            </div>
            <div className="rounded-lg border border-border bg-card p-4">
              <h3 className="text-xs font-medium mb-2">按阶段</h3>
              <div className="space-y-1.5">
                {Object.entries(graph.stats.phases).map(([phase, count]) => (
                  <div key={phase} className="flex items-center justify-between">
                    <Badge className={`${PHASE_COLORS[phase] || 'bg-muted text-muted-foreground'}`}>{phase}</Badge>
                    <span className="text-xs font-medium">{count}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Capabilities View */}
      {view === 'capabilities' && capabilities && (
        <div className="rounded-lg border border-border bg-card p-4">
          <h3 className="text-xs font-medium mb-3">能力索引 ({capabilities.total_capabilities})</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
            {Object.entries(capabilities.capabilities).sort(([a], [b]) => a.localeCompare(b)).map(([cap, comps]) => (
              <div key={cap} className="rounded-md border border-border p-2 hover:bg-muted/30 transition-colors">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-primary">{cap}</span>
                  <span className="text-[10px] text-muted-foreground">{comps.length}个组件</span>
                </div>
                <div className="flex flex-wrap gap-1 mt-1">
                  {comps.map((compId) => {
                    const comp = components.find((c) => c.id === compId);
                    return (
                      <Badge variant="muted" key={compId}>
                        {comp?.emoji} {comp?.name || compId}
                      </Badge>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Footer */}
      <div className="text-[10px] text-muted-foreground text-center">
        组件注册中心 · API: /api/registry · {components.length} 个组件
      </div>
    </div>
  );
}
