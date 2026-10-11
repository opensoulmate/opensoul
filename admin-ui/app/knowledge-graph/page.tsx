'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, RefreshCw, X, Plus, Trash2, Network, CircleDot, GitBranch,
  ZoomIn, ZoomOut, Maximize2, Filter, Eye,
} from 'lucide-react';
import { Tab, TabBar } from '@/components/ui/tab';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

// ── Types ───────────────────────────────────────────────────────

interface GraphNode {
  id: string;
  label: string;
  node_type: string;
  properties: Record<string, unknown>;
}

interface GraphEdge {
  source: string;
  target: string;
  relation_type: string;
  properties: Record<string, unknown>;
}

interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

interface EntityDetail {
  id: string;
  name: string;
  type: string;
  description: string;
  properties: Record<string, unknown>;
  relations?: Array<{
    id: string;
    source_entity_id: string;
    target_entity_id: string;
    relation_type: string;
    properties: Record<string, unknown>;
  }>;
}

interface GraphStats {
  total_entities: number;
  total_relations: number;
  by_type: Record<string, number>;
}

// ── Force-directed layout ───────────────────────────────────────

interface LayoutNode extends GraphNode {
  x: number;
  y: number;
  vx: number;
  vy: number;
}

const TYPE_COLORS: Record<string, string> = {
  person: '#3b82f6',
  organization: '#f59e0b',
  product: '#10b981',
  technology: '#8b5cf6',
  concept: '#ec4899',
  location: '#06b6d4',
  amount: '#f97316',
  other: '#6b7280',
};

function getColor(type: string): string {
  return TYPE_COLORS[type] || TYPE_COLORS.other;
}

// ── Canvas Graph Component ──────────────────────────────────────

function ForceGraph({
  data,
  onNodeClick,
  highlightId,
}: {
  data: GraphData;
  onNodeClick: (id: string) => void;
  highlightId: string | null;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const nodesRef = useRef<LayoutNode[]>([]);
  const animRef = useRef<number>(0);
  const transformRef = useRef({ x: 0, y: 0, scale: 1 });
  const dragRef = useRef<{ node: LayoutNode | null; offsetX: number; offsetY: number; panning: boolean; panStartX: number; panStartY: number }>({
    node: null, offsetX: 0, offsetY: 0, panning: false, panStartX: 0, panStartY: 0,
  });
  const [zoom, setZoom] = useState(1);

  // Initialize layout nodes
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const w = canvas.width;
    const h = canvas.height;
    const existing = new Map(nodesRef.current.map((n) => [n.id, { x: n.x, y: n.y }]));
    nodesRef.current = data.nodes.map((n) => {
      const prev = existing.get(n.id);
      return {
        ...n,
        x: prev?.x ?? w / 2 + (Math.random() - 0.5) * w * 0.6,
        y: prev?.y ?? h / 2 + (Math.random() - 0.5) * h * 0.6,
        vx: 0,
        vy: 0,
      };
    });
  }, [data.nodes]);

  // Force simulation + render loop
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d')!;

    const resize = () => {
      const rect = canvas.parentElement!.getBoundingClientRect();
      canvas.width = rect.width * devicePixelRatio;
      canvas.height = rect.height * devicePixelRatio;
      canvas.style.width = `${rect.width}px`;
      canvas.style.height = `${rect.height}px`;
    };
    resize();
    window.addEventListener('resize', resize);

    const nodeMap = new Map(nodesRef.current.map((n) => [n.id, n]));

    const tick = () => {
      const nodes = nodesRef.current;
      const edges = data.edges;
      const alpha = 0.3;

      // Repulsion
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          let dx = nodes[j].x - nodes[i].x;
          let dy = nodes[j].y - nodes[i].y;
          let dist = Math.sqrt(dx * dx + dy * dy) || 1;
          if (dist < 300) {
            const force = (300 - dist) / dist * 0.5;
            nodes[i].vx -= dx * force;
            nodes[i].vy -= dy * force;
            nodes[j].vx += dx * force;
            nodes[j].vy += dy * force;
          }
        }
      }

      // Attraction along edges
      for (const edge of edges) {
        const s = nodeMap.get(edge.source);
        const t = nodeMap.get(edge.target);
        if (!s || !t) continue;
        const dx = t.x - s.x;
        const dy = t.y - s.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const force = (dist - 150) * 0.005;
        s.vx += dx * force;
        s.vy += dy * force;
        t.vx -= dx * force;
        t.vy -= dy * force;
      }

      // Center gravity
      const cx = canvas.width / devicePixelRatio / 2;
      const cy = canvas.height / devicePixelRatio / 2;
      for (const n of nodes) {
        n.vx += (cx - n.x) * 0.001;
        n.vy += (cy - n.y) * 0.001;
        n.vx *= 0.85;
        n.vy *= 0.85;
        n.x += n.vx * alpha;
        n.y += n.vy * alpha;
      }

      // Draw
      const dpr = devicePixelRatio;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, canvas.width / dpr, canvas.height / dpr);

      const t = transformRef.current;
      ctx.save();
      ctx.translate(t.x, t.y);
      ctx.scale(t.scale, t.scale);

      // Edges
      for (const edge of edges) {
        const s = nodeMap.get(edge.source);
        const tg = nodeMap.get(edge.target);
        if (!s || !tg) continue;
        ctx.beginPath();
        ctx.moveTo(s.x, s.y);
        ctx.lineTo(tg.x, tg.y);
        ctx.strokeStyle = 'rgba(148,163,184,0.3)';
        ctx.lineWidth = 1;
        ctx.stroke();

        // Edge label
        const mx = (s.x + tg.x) / 2;
        const my = (s.y + tg.y) / 2;
        ctx.font = '9px sans-serif';
        ctx.fillStyle = 'rgba(148,163,184,0.7)';
        ctx.textAlign = 'center';
        ctx.fillText(edge.relation_type, mx, my - 4);
      }

      // Nodes
      for (const n of nodes) {
        const isHighlight = highlightId === n.id;
        const r = isHighlight ? 18 : 12;
        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx.fillStyle = getColor(n.node_type);
        if (isHighlight) {
          ctx.shadowColor = getColor(n.node_type);
          ctx.shadowBlur = 16;
        }
        ctx.fill();
        ctx.shadowBlur = 0;
        ctx.strokeStyle = isHighlight ? '#fff' : 'rgba(0,0,0,0.2)';
        ctx.lineWidth = isHighlight ? 2.5 : 1;
        ctx.stroke();

        // Label
        ctx.font = `${isHighlight ? '11' : '10'}px sans-serif`;
        ctx.fillStyle = isHighlight ? '#fff' : 'rgba(255,255,255,0.9)';
        ctx.textAlign = 'center';
        ctx.fillText(n.label.slice(0, 12), n.x, n.y + r + 12);
      }

      ctx.restore();
      animRef.current = requestAnimationFrame(tick);
    };

    animRef.current = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(animRef.current);
      window.removeEventListener('resize', resize);
    };
  }, [data.edges, highlightId]);

  // Mouse interaction
  const handleMouseDown = (e: React.MouseEvent) => {
    const canvas = canvasRef.current!;
    const rect = canvas.getBoundingClientRect();
    const t = transformRef.current;
    const mx = (e.clientX - rect.left - t.x) / t.scale;
    const my = (e.clientY - rect.top - t.y) / t.scale;

    for (const n of nodesRef.current) {
      const dx = n.x - mx;
      const dy = n.y - my;
      if (dx * dx + dy * dy < 225) {
        dragRef.current = { node: n, offsetX: mx - n.x, offsetY: my - n.y, panning: false, panStartX: 0, panStartY: 0 };
        return;
      }
    }
    dragRef.current = { node: null, offsetX: 0, offsetY: 0, panning: true, panStartX: e.clientX - t.x, panStartY: e.clientY - t.y };
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    const d = dragRef.current;
    const t = transformRef.current;
    if (d.node) {
      const canvas = canvasRef.current!;
      const rect = canvas.getBoundingClientRect();
      const mx = (e.clientX - rect.left - t.x) / t.scale;
      const my = (e.clientY - rect.top - t.y) / t.scale;
      d.node.x = mx - d.offsetX;
      d.node.y = my - d.offsetY;
      d.node.vx = 0;
      d.node.vy = 0;
    } else if (d.panning) {
      t.x = e.clientX - d.panStartX;
      t.y = e.clientY - d.panStartY;
    }
  };

  const handleMouseUp = (e: React.MouseEvent) => {
    const d = dragRef.current;
    if (d.node) {
      onNodeClick(d.node.id);
    }
    d.node = null;
    d.panning = false;
  };

  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const t = transformRef.current;
    const factor = e.deltaY < 0 ? 1.1 : 0.9;
    const canvas = canvasRef.current!;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    t.x = mx - (mx - t.x) * factor;
    t.y = my - (my - t.y) * factor;
    t.scale *= factor;
    setZoom(t.scale);
  };

  const resetView = () => {
    transformRef.current = { x: 0, y: 0, scale: 1 };
    setZoom(1);
  };

  const zoomIn = () => {
    const t = transformRef.current;
    t.scale *= 1.2;
    setZoom(t.scale);
  };

  const zoomOut = () => {
    const t = transformRef.current;
    t.scale *= 0.8;
    setZoom(t.scale);
  };

  return (
    <div className="relative w-full h-full">
      <canvas
        ref={canvasRef}
        className="w-full h-full cursor-grab active:cursor-grabbing"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onWheel={handleWheel}
      />
      <div className="absolute top-2 right-2 flex flex-col gap-1">
        <Button onClick={zoomIn}  title="放大" variant="secondary" className="p-1.5 rounded-md bg-card/80">
          <ZoomIn className="w-3.5 h-3.5" />
        </Button>
        <Button onClick={zoomOut}  title="缩小" variant="secondary" className="p-1.5 rounded-md bg-card/80">
          <ZoomOut className="w-3.5 h-3.5" />
        </Button>
        <Button onClick={resetView}  title="重置视图" variant="secondary" className="p-1.5 rounded-md bg-card/80">
          <Maximize2 className="w-3.5 h-3.5" />
        </Button>
      </div>
      <div className="absolute bottom-2 left-2 text-[10px] text-muted-foreground bg-card/80 px-2 py-1 rounded">
        {Math.round(zoom * 100)}% · {data.nodes.length} 节点 · {data.edges.length} 边
      </div>
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────────

export default function KnowledgeGraphPage() {
  const [graphData, setGraphData] = useState<GraphData>({ nodes: [], edges: [] });
  const [stats, setStats] = useState<GraphStats | null>(null);
  const [entities, setEntities] = useState<EntityDetail[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState('all');
  const [viewMode, setViewMode] = useState<'graph' | 'list'>('graph');
  const [selectedEntity, setSelectedEntity] = useState<EntityDetail | null>(null);
  const [showDetail, setShowDetail] = useState(false);
  const [showCreateRelation, setShowCreateRelation] = useState(false);
  const [relationForm, setRelationForm] = useState({ source_entity_id: '', target_entity_id: '', relation_type: 'related' });
  const [creating, setCreating] = useState(false);
  const [depth, setDepth] = useState(2);

  const fetchAll = useCallback(async () => {
    try {
      setLoading(true);
      const [graphRes, statsRes, entitiesRes] = await Promise.all([
        apiFetch(`/api/graph/?depth=${depth}`),
        apiFetch('/api/graph/stats'),
        apiFetch('/api/graph/entities?limit=200'),
      ]);
      setGraphData(graphRes);
      setStats(statsRes);
      setEntities(Array.isArray(entitiesRes) ? entitiesRes : []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [depth]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const fetchEntityDetail = async (id: string) => {
    try {
      const data = await apiFetch(`/api/graph/entities/${id}`);
      setSelectedEntity(data);
      setShowDetail(true);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleCreateRelation = async () => {
    if (!relationForm.source_entity_id || !relationForm.target_entity_id) return;
    try {
      setCreating(true);
      await apiFetch('/api/graph/relations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(relationForm),
      });
      setShowCreateRelation(false);
      setRelationForm({ source_entity_id: '', target_entity_id: '', relation_type: 'related' });
      await fetchAll();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCreating(false);
    }
  };

  const filteredEntities = entities.filter((e) => {
    if (typeFilter !== 'all' && e.type !== typeFilter) return false;
    if (search) {
      const q = search.toLowerCase();
      return e.name.toLowerCase().includes(q) || e.type.toLowerCase().includes(q);
    }
    return true;
  });

  const entityTypes = stats?.by_type ? Object.entries(stats.by_type).sort((a, b) => b[1] - a[1]) : [];

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4 h-[calc(100vh-8rem)]">
      {error && (
        <div className="flex items-center gap-2 text-xs text-red-500 bg-red-500/10 px-3 py-2 rounded-md">
          {error}
          <Button variant="bare" onClick={() => setError('')} className="ml-auto"><X className="w-3 h-3" /></Button>
        </div>
      )}

      {/* Stats bar */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{stats?.total_entities ?? 0}</div>
          <div className="text-xs text-muted-foreground">实体总数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-primary">{stats?.total_relations ?? 0}</div>
          <div className="text-xs text-muted-foreground">关系总数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-emerald-600">{entityTypes.length}</div>
          <div className="text-xs text-muted-foreground">实体类型</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex gap-1 flex-wrap mt-1">
            {entityTypes.slice(0, 4).map(([type, cnt]) => (
              <Badge variant="bare" key={type} className="gap-1" style={{ backgroundColor: `${getColor(type)}20`, color: getColor(type) }}>
                <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: getColor(type) }} />
                {type} ({cnt})
              </Badge>
            ))}
          </div>
          <div className="text-xs text-muted-foreground mt-1">类型分布</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索实体名称..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
        >
          <option value="all">全部类型</option>
          {entityTypes.map(([type]) => (
            <option key={type} value={type}>{type}</option>
          ))}
        </select>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={depth}
          onChange={(e) => setDepth(Number(e.target.value))}
        >
          <option value={1}>深度 1</option>
          <option value={2}>深度 2</option>
          <option value={3}>深度 3</option>
        </select>
        <TabBar>
          <Tab active={viewMode === 'graph'} onClick={() => setViewMode('graph')}>
            <Network className="w-3.5 h-3.5" />
          </Tab>
          <Tab active={viewMode === 'list'} onClick={() => setViewMode('list')}>
            <Filter className="w-3.5 h-3.5" />
          </Tab>
        </TabBar>
        <Button onClick={() => setShowCreateRelation(true)} variant="primary" className="gap-1.5">
          <Plus className="w-3.5 h-3.5" />添加关系
        </Button>
        <Button onClick={() => fetchAll()} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
      </div>

      {/* Content */}
      {viewMode === 'graph' ? (
        <div className="rounded-lg border border-border bg-card overflow-hidden" style={{ height: 'calc(100% - 220px)', minHeight: '400px' }}>
          {graphData.nodes.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
              <Network className="w-12 h-12 mb-2 opacity-30" />
              <p className="text-sm">暂无知识图谱数据</p>
              <p className="text-xs mt-1">添加知识条目后自动构建图谱</p>
            </div>
          ) : (
            <ForceGraph
              data={graphData}
              onNodeClick={fetchEntityDetail}
              highlightId={selectedEntity?.id ?? null}
            />
          )}
        </div>
      ) : (
        <div className="rounded-lg border border-border overflow-hidden" style={{ height: 'calc(100% - 220px)', minHeight: '400px' }}>
          <div className="overflow-auto h-full">
            <table className="w-full text-xs">
              <thead className="bg-muted/50 sticky top-0">
                <tr>
                  <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">名称</th>
                  <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">类型</th>
                  <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">描述</th>
                  <th className="text-right px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">操作</th>
                </tr>
              </thead>
              <tbody>
                {filteredEntities.map((entity) => (
                  <tr key={entity.id} className="border-b border-border last:border-0 hover:bg-muted/30">
                    <td className="px-2 lg:px-4 py-2.5 font-medium">
                      <span className="inline-flex items-center gap-1.5">
                        <CircleDot className="w-3 h-3" style={{ color: getColor(entity.type) }} />
                        {entity.name}
                      </span>
                    </td>
                    <td className="px-2 lg:px-4 py-2.5">
                      <Badge style={{ backgroundColor: `${getColor(entity.type)}20`, color: getColor(entity.type) }} variant="default">
                        {entity.type}
                      </Badge>
                    </td>
                    <td className="px-2 lg:px-4 py-2.5 text-muted-foreground max-w-xs truncate">{entity.description || '-'}</td>
                    <td className="px-2 lg:px-4 py-2.5 text-right">
                      <Button
                        onClick={() => fetchEntityDetail(entity.id)}
                        variant="icon-sm" className="hover:bg-muted"
                        title="查看详情"
                      >
                        <Eye className="w-3.5 h-3.5" />
                      </Button>
                    </td>
                  </tr>
                ))}
                {filteredEntities.length === 0 && (
                  <tr>
                    <td colSpan={4} className="px-2 lg:px-4 py-8 text-center text-muted-foreground">
                      <CircleDot className="w-8 h-8 mx-auto mb-2 opacity-30" />
                      <p>没有匹配的实体</p>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Entity Detail Dialog */}
      {showDetail && selectedEntity && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowDetail(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4 max-h-[80vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <div className="flex items-center gap-2">
                <CircleDot className="w-5 h-5" style={{ color: getColor(selectedEntity.type) }} />
                <h2 className="text-base font-medium">{selectedEntity.name}</h2>
                <Badge style={{ backgroundColor: `${getColor(selectedEntity.type)}20`, color: getColor(selectedEntity.type) }} variant="default">
                  {selectedEntity.type}
                </Badge>
              </div>
              <Button onClick={() => setShowDetail(false)} variant="icon-sm" className="hover:bg-muted">
                <X className="w-4 h-4" />
              </Button>
            </div>
            <div className="p-4 space-y-3">
              {selectedEntity.description && (
                <div>
                  <div className="text-xs text-muted-foreground">描述</div>
                  <div className="text-sm mt-0.5">{selectedEntity.description}</div>
                </div>
              )}
              <div>
                <div className="text-xs text-muted-foreground">ID</div>
                <div className="text-xs mt-0.5 font-mono text-muted-foreground break-all">{selectedEntity.id}</div>
              </div>
              {selectedEntity.relations && selectedEntity.relations.length > 0 && (
                <div>
                  <div className="text-xs text-muted-foreground mb-2">关系 ({selectedEntity.relations.length})</div>
                  <div className="space-y-1.5">
                    {selectedEntity.relations.map((rel) => {
                      const isSource = rel.source_entity_id === selectedEntity.id;
                      return (
                        <div key={rel.id} className="flex items-center gap-2 text-xs p-2 rounded bg-muted/50">
                          <GitBranch className="w-3 h-3 text-muted-foreground shrink-0" />
                          <Badge variant="bare" className="font-mono bg-primary/10 text-primary">
                            {rel.relation_type}
                          </Badge>
                          <span className="text-muted-foreground">
                            {isSource ? '→' : '←'} {isSource ? rel.target_entity_id.slice(0, 8) : rel.source_entity_id.slice(0, 8)}...
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
              {Object.keys(selectedEntity.properties || {}).length > 0 && (
                <div>
                  <div className="text-xs text-muted-foreground mb-1">属性</div>
                  <pre className="text-xs bg-muted p-2 rounded overflow-x-auto">{JSON.stringify(selectedEntity.properties, null, 2)}</pre>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Create Relation Dialog */}
      {showCreateRelation && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowCreateRelation(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-md mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-base font-medium">添加关系</h2>
              <Button onClick={() => setShowCreateRelation(false)} variant="icon-sm" className="hover:bg-muted">
                <X className="w-4 h-4" />
              </Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">源实体</label>
                <select
                  className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
                  value={relationForm.source_entity_id}
                  onChange={(e) => setRelationForm((f) => ({ ...f, source_entity_id: e.target.value }))}
                >
                  <option value="">选择实体...</option>
                  {entities.map((e) => (
                    <option key={e.id} value={e.id}>{e.name} ({e.type})</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">目标实体</label>
                <select
                  className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
                  value={relationForm.target_entity_id}
                  onChange={(e) => setRelationForm((f) => ({ ...f, target_entity_id: e.target.value }))}
                >
                  <option value="">选择实体...</option>
                  {entities.map((e) => (
                    <option key={e.id} value={e.id}>{e.name} ({e.type})</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">关系类型</label>
                <select
                  className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
                  value={relationForm.relation_type}
                  onChange={(e) => setRelationForm((f) => ({ ...f, relation_type: e.target.value }))}
                >
                  {['related', 'belongs_to', 'produces', 'uses', 'competes_with', 'cooperates_with', 'contains', 'recommends', 'invests_in', 'located_in', 'created', 'is_a'].map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowCreateRelation(false)} variant="secondary">
                  取消
                </Button>
                <Button onClick={handleCreateRelation}
                  disabled={creating || !relationForm.source_entity_id || !relationForm.target_entity_id} variant="primary">
                  {creating ? '创建中...' : '创建关系'}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
