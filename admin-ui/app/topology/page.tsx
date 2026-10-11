'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  RefreshCw, X, AlertCircle, Loader2, Network, Activity,
  ChevronRight, Layers, GitBranch
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

interface TopoNode {
  id: string;
  name: string;
  category: string;
  layer: string;
  emoji: string;
  health: string;
  response_time_ms: number;
}

interface TopoEdge {
  from: string;
  to: string;
}

interface TopoGraph {
  nodes: TopoNode[];
  edges: TopoEdge[];
  stats: { total: number; healthy: number; unhealthy: number; unknown: number; total_edges: number };
  timestamp: number;
}

interface Clusters {
  clusters: Record<string, { id: string; name: string; emoji: string; layer: string }[]>;
}

interface Dependencies {
  component: string;
  depends_on: string[];
  depended_by: string[];
  direct_connections: number;
}

const CATEGORY_LABELS: Record<string, string> = {
  core: '核心',
  service: '服务',
  organ: '器官',
  system: '系统',
};

const CATEGORY_COLORS: Record<string, string> = {
  core: 'border-blue-500/40 bg-blue-500/5',
  service: 'border-green-500/40 bg-green-500/5',
  organ: 'border-purple-500/40 bg-purple-500/5',
  system: 'border-orange-500/40 bg-orange-500/5',
};

export default function TopologyPage() {
  const [graph, setGraph] = useState<TopoGraph | null>(null);
  const [clusters, setClusters] = useState<Clusters | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedNode, setSelectedNode] = useState<TopoNode | null>(null);
  const [dependencies, setDependencies] = useState<Dependencies | null>(null);
  const [showDetail, setShowDetail] = useState(false);
  const [viewMode, setViewMode] = useState<'graph' | 'clusters'>('graph');

  const fetchGraph = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiFetch('/api/topology/graph');
      setGraph(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchClusters = useCallback(async () => {
    try {
      const data = await apiFetch('/api/topology/clusters');
      setClusters(data);
    } catch { /* silent */ }
  }, []);

  useEffect(() => { fetchGraph(); fetchClusters(); }, [fetchGraph, fetchClusters]);

  const handleNodeClick = async (node: TopoNode) => {
    setSelectedNode(node);
    setShowDetail(true);
    try {
      const data = await apiFetch(`/api/topology/dependencies/${node.id}`);
      setDependencies(data);
    } catch { setDependencies(null); }
  };

  // Simple SVG graph layout — nodes arranged in a circle with edges
  const nodePositions = graph ? (() => {
    const cx = 400, cy = 250, rx = 300, ry = 200;
    const positions: Record<string, { x: number; y: number }> = {};
    graph.nodes.forEach((n, i) => {
      const angle = (2 * Math.PI * i) / graph.nodes.length - Math.PI / 2;
      positions[n.id] = { x: cx + rx * Math.cos(angle), y: cy + ry * Math.sin(angle) };
    });
    return positions;
  })() : {};

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-500">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <Button onClick={() => setError('')} variant="icon-xs" className="hover:bg-red-500/20"><X className="w-3.5 h-3.5" /></Button>
        </div>
      )}

      {/* Stats */}
      {graph && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-2xl font-bold">{graph.stats.total}</div>
            <div className="text-xs text-muted-foreground">组件总数</div>
          </div>
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-2xl font-bold text-emerald-500">{graph.stats.healthy}</div>
            <div className="text-xs text-muted-foreground">健康</div>
          </div>
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-2xl font-bold text-red-500">{graph.stats.unhealthy}</div>
            <div className="text-xs text-muted-foreground">异常</div>
          </div>
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-2xl font-bold text-gray-500">{graph.stats.unknown}</div>
            <div className="text-xs text-muted-foreground">未知</div>
          </div>
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-2xl font-bold text-blue-500">{graph.stats.total_edges}</div>
            <div className="text-xs text-muted-foreground">依赖边</div>
          </div>
        </div>
      )}

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1">
          <Button onClick={() => setViewMode('graph')} variant="chip-sm" className={`border  ${viewMode === 'graph' ? 'bg-primary/10 border-primary/30 text-primary' : 'bg-muted border-border text-muted-foreground'}`}>
            <Network className="w-3.5 h-3.5 inline mr-1" />拓扑图
          </Button>
          <Button onClick={() => setViewMode('clusters')} variant="chip-sm" className={`border  ${viewMode === 'clusters' ? 'bg-primary/10 border-primary/30 text-primary' : 'bg-muted border-border text-muted-foreground'}`}>
            <Layers className="w-3.5 h-3.5 inline mr-1" />集群视图
          </Button>
        </div>
        <div className="flex-1" />
        <Button onClick={fetchGraph} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
      </div>

      {/* Graph View */}
      {viewMode === 'graph' && graph && (
        <div className="rounded-lg border border-border bg-card overflow-hidden">
          <svg viewBox="0 0 800 500" className="w-full h-auto" style={{ maxHeight: '60vh' }}>
            {/* Edges */}
            {graph.edges.map((edge, i) => {
              const from = nodePositions[edge.from];
              const to = nodePositions[edge.to];
              if (!from || !to) return null;
              return (
                <line key={i} x1={from.x} y1={from.y} x2={to.x} y2={to.y}
                  stroke="var(--border, #333)" strokeWidth="1" opacity="0.3"
                  markerEnd="url(#arrow)" />
              );
            })}
            <defs>
              <marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted-foreground, #888)" opacity="0.5" />
              </marker>
            </defs>

            {/* Nodes */}
            {graph.nodes.map((node) => {
              const pos = nodePositions[node.id];
              if (!pos) return null;
              const isHealthy = node.health === 'ok';
              const isSelected = selectedNode?.id === node.id;
              return (
                <g key={node.id} onClick={() => handleNodeClick(node)} className="cursor-pointer">
                  <circle cx={pos.x} cy={pos.y} r={isSelected ? 24 : 20}
                    fill={isHealthy ? 'var(--card, #1a1a1a)' : 'rgba(239,68,68,0.1)'}
                    stroke={isSelected ? 'var(--primary, #666)' : isHealthy ? 'var(--border, #333)' : '#ef4444'}
                    strokeWidth={isSelected ? 2.5 : 1.5} />
                  {/* Health indicator dot */}
                  <circle cx={pos.x + 14} cy={pos.y - 14} r={4}
                    fill={isHealthy ? '#22c55e' : node.health === 'unknown' ? '#9ca3af' : '#ef4444'} />
                  <text x={pos.x} y={pos.y - 2} textAnchor="middle" fontSize="14">{node.emoji}</text>
                  <text x={pos.x} y={pos.y + 14} textAnchor="middle" fontSize="7" fill="var(--foreground, #fff)" fontWeight="500">
                    {node.name.replace('Open', '')}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>
      )}

      {/* Clusters View */}
      {viewMode === 'clusters' && clusters && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {Object.entries(clusters.clusters).map(([cat, nodes]) => (
            <div key={cat} className={`rounded-lg border bg-card p-4 ${CATEGORY_COLORS[cat] || 'border-border'}`}>
              <div className="flex items-center gap-2 mb-3">
                <span className="text-xs font-medium">{CATEGORY_LABELS[cat] || cat}</span>
                <Badge variant="muted">{nodes.length} 个组件</Badge>
              </div>
              <div className="space-y-1.5">
                {nodes.map((n) => {
                  const graphNode = graph?.nodes.find((gn) => gn.id === n.id);
                  const isHealthy = graphNode?.health === 'ok';
                  return (
                    <div key={n.id} onClick={() => graphNode && handleNodeClick(graphNode)}
                      className="flex items-center gap-2 px-3 py-2 rounded-md hover:bg-muted/30 cursor-pointer transition-colors">
                      <span className="text-base">{n.emoji}</span>
                      <span className="text-xs font-medium flex-1">{n.name}</span>
                      <span className="text-[10px] text-muted-foreground">{n.layer}</span>
                      <div className={`w-2 h-2 rounded-full ${isHealthy ? 'bg-green-500' : graphNode?.health === 'unknown' ? 'bg-gray-400' : 'bg-red-500'}`} />
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Node Detail Dialog */}
      {showDetail && selectedNode && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowDetail(false)}>
          <div className="bg-card border border-border rounded-xl shadow-xl w-full max-w-md mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <div className="flex items-center gap-2">
                <span className="text-xl">{selectedNode.emoji}</span>
                <h2 className="text-sm font-semibold">{selectedNode.name}</h2>
              </div>
              <Button onClick={() => setShowDetail(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <span className="text-xs text-muted-foreground">类别</span>
                  <div className="text-sm">{CATEGORY_LABELS[selectedNode.category] || selectedNode.category}</div>
                </div>
                <div>
                  <span className="text-xs text-muted-foreground">层级</span>
                  <div className="text-sm">{selectedNode.layer}</div>
                </div>
                <div>
                  <span className="text-xs text-muted-foreground">健康状态</span>
                  <div className={`text-sm font-medium ${selectedNode.health === 'ok' ? 'text-emerald-500' : 'text-red-500'}`}>
                    {selectedNode.health === 'ok' ? '健康' : selectedNode.health === 'unknown' ? '未知' : '异常'}
                  </div>
                </div>
                <div>
                  <span className="text-xs text-muted-foreground">响应时间</span>
                  <div className="text-sm">{selectedNode.response_time_ms?.toFixed(1) ?? '-'}ms</div>
                </div>
              </div>

              {dependencies && (
                <div className="space-y-2">
                  {dependencies.depends_on.length > 0 && (
                    <div>
                      <span className="text-xs text-muted-foreground">依赖 ({dependencies.depends_on.length})</span>
                      <div className="flex flex-wrap gap-1 mt-1">
                        {dependencies.depends_on.map((dep) => (
                          <Badge key={dep} variant="info">{dep}</Badge>
                        ))}
                      </div>
                    </div>
                  )}
                  {dependencies.depended_by.length > 0 && (
                    <div>
                      <span className="text-xs text-muted-foreground">被依赖 ({dependencies.depended_by.length})</span>
                      <div className="flex flex-wrap gap-1 mt-1">
                        {dependencies.depended_by.map((dep) => (
                          <Badge key={dep} variant="accent">{dep}</Badge>
                        ))}
                      </div>
                    </div>
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
