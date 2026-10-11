'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  RefreshCw, Dna, GitBranch, ArrowRight, CheckCircle, XCircle, Clock,
  AlertTriangle, ChevronDown, ChevronRight, Search, X, Play, RotateCcw,
  Package, Layers, FileText, Activity, Loader2, Tag, ArrowUpRight,
  Diff, Code, BookOpen,
} from 'lucide-react';
import { Badge, TONES } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

// ── Types ───────────────────────────────────────────────────

interface ComponentInfo {
  component_id: string;
  component_name: string;
  version: string;
  status: string;
  created_at: string;
  dependencies?: Record<string, string>;
}

interface ComponentDetail {
  component_id: string;
  current: {
    version: string;
    status: string;
    dependencies: Record<string, string>;
    release_notes: string;
    breaking_changes: string[];
    created_at: string;
  };
  versions: { version: string; status: string; created_at: string }[];
  compatibility: Record<string, any>;
}

interface MigrationInfo {
  migration_id: string;
  component_id: string;
  from_version: string;
  to_version: string;
  status: string;
  dry_run: boolean;
  started_at: string;
  completed_at: string;
  error: string;
}

interface SchemaDiff {
  component_id: string;
  from_version: string;
  to_version: string;
  added: { name: string; field_type: string }[];
  removed: string[];
  modified: string[];
}

interface ChangelogEntry {
  component_id: string;
  version: string;
  action: string;
  timestamp: string;
  details?: string;
}

interface DepGraph {
  [key: string]: { name: string; version: string; dependencies: Record<string, string> };
}

interface PlatformInfo {
  platform_version: string;
  components_count: number;
  latest_changelog?: ChangelogEntry;
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

const STATUS_CONFIG: Record<string, { icon: any; color: string; label: string }> = {
  active: { icon: CheckCircle, color: TONES.success.text, label: '活跃' },
  stable: { icon: CheckCircle, color: TONES.success.text, label: '稳定' },
  deprecated: { icon: AlertTriangle, color: TONES.warning.text, label: '废弃' },
  beta: { icon: Clock, color: TONES.info.text, label: '测试' },
  alpha: { icon: AlertTriangle, color: TONES.warning.text, label: '内测' },
  pending: { icon: Clock, color: TONES.muted.text, label: '待定' },
  completed: { icon: CheckCircle, color: TONES.success.text, label: '完成' },
  executing: { icon: Loader2, color: TONES.info.text, label: '执行中' },
  failed: { icon: XCircle, color: TONES.destructive.text, label: '失败' },
  rolled_back: { icon: RotateCcw, color: TONES.warning.text, label: '已回滚' },
};

function StatusBadge({ status }: { status: string }) {
  const cfg = STATUS_CONFIG[status] || { icon: Clock, color: TONES.muted.text, label: status };
  const Icon = cfg.icon;
  return (
    <Badge variant="bare" className={`gap-1 border border-current/20 ${cfg.color}`}>
      <Icon className={`w-3 h-3 ${status === 'executing' ? 'animate-spin' : ''}`} />
      {cfg.label}
    </Badge>
  );
}

// ── Component Card ──────────────────────────────────────────

function ComponentCard({ comp, expanded, onToggle, detail, loadingDetail }: {
  comp: ComponentInfo;
  expanded: boolean;
  onToggle: () => void;
  detail: ComponentDetail | null;
  loadingDetail: boolean;
}) {
  const deps = comp.dependencies ? Object.entries(comp.dependencies) : [];
  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      <Button
        onClick={onToggle}
        variant="row" className="gap-3 p-4 hover:bg-muted/30 transition-colors"
      >
        <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
          <Package className="w-4 h-4 text-primary" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium truncate">{comp.component_name}</span>
            <span className="text-[10px] text-muted-foreground font-mono">v{comp.version}</span>
            <StatusBadge status={comp.status} />
          </div>
          <div className="text-[10px] text-muted-foreground mt-0.5">
            {comp.component_id}
            {deps.length > 0 && ` · 依赖 ${deps.length} 个组件`}
          </div>
        </div>
        {expanded ? <ChevronDown className="w-4 h-4 text-muted-foreground shrink-0" /> : <ChevronRight className="w-4 h-4 text-muted-foreground shrink-0" />}
      </Button>

      {expanded && (
        <div className="border-t border-border p-4 space-y-3">
          {loadingDetail ? (
            <div className="text-xs text-muted-foreground flex items-center gap-2"><Loader2 className="w-3 h-3 animate-spin" />加载详情...</div>
          ) : detail ? (
            <>
              {/* Version history */}
              {detail.versions.length > 1 && (
                <div>
                  <div className="text-xs font-medium text-muted-foreground mb-2">版本历史</div>
                  <div className="flex flex-wrap gap-1.5">
                    {detail.versions.map((v, i) => (
                      <Badge variant="bare" key={v.version} className={`border ${i === 0 ? 'bg-primary/10 text-primary border-primary/20' : 'bg-muted text-muted-foreground border-border'}`}>
                        v{v.version} <StatusBadge status={v.status} />
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {/* Dependencies */}
              {Object.keys(detail.current.dependencies).length > 0 && (
                <div>
                  <div className="text-xs font-medium text-muted-foreground mb-2">依赖关系</div>
                  <div className="flex flex-wrap gap-1.5">
                    {Object.entries(detail.current.dependencies).map(([dep, ver]) => (
                      <Badge key={dep} variant="info" className="border border-blue-500/20">
                        {dep} {ver}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {/* Release notes */}
              {detail.current.release_notes && (
                <div>
                  <div className="text-xs font-medium text-muted-foreground mb-1">发布说明</div>
                  <p className="text-xs text-muted-foreground whitespace-pre-wrap">{detail.current.release_notes}</p>
                </div>
              )}

              {/* Breaking changes */}
              {detail.current.breaking_changes.length > 0 && (
                <div>
                  <div className="text-xs font-medium text-red-500 mb-1">⚠ 破坏性变更</div>
                  <ul className="list-disc list-inside text-xs text-red-400 space-y-0.5">
                    {detail.current.breaking_changes.map((bc, i) => <li key={i}>{bc}</li>)}
                  </ul>
                </div>
              )}

              {/* Compatibility */}
              {detail.compatibility && Object.keys(detail.compatibility).length > 0 && (
                <div>
                  <div className="text-xs font-medium text-muted-foreground mb-2">兼容性</div>
                  <div className="grid grid-cols-2 gap-1.5">
                    {Object.entries(detail.compatibility).map(([k, v]) => (
                      <div key={k} className="flex items-center gap-1.5 text-[10px]">
                        {typeof v === 'object' && v !== null && (v as any).compatible === false ? (
                          <XCircle className="w-3 h-3 text-red-500" />
                        ) : (
                          <CheckCircle className="w-3 h-3 text-green-500" />
                        )}
                        <span className="text-muted-foreground">{k}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="text-xs text-muted-foreground">暂无详情</div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Migration Card ──────────────────────────────────────────

function MigrationCard({ m, onExecute, onRollback, executing }: {
  m: MigrationInfo;
  onExecute: () => void;
  onRollback: () => void;
  executing: boolean;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono text-muted-foreground">{m.migration_id.slice(0, 8)}</span>
          <StatusBadge status={m.status} />
          {m.dry_run && <Badge variant="warning" className="border border-amber-500/20">试运行</Badge>}
        </div>
        <div className="flex items-center gap-1">
          {(m.status === 'pending' || m.status === 'failed') && (
            <Button
              onClick={onExecute}
              disabled={executing}
              variant="chip-xs" className="flex items-center gap-1 bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20 disabled:opacity-50"
            >
              {executing ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
              执行
            </Button>
          )}
          {m.status === 'completed' && (
            <Button
              onClick={onRollback}
              disabled={executing}
              variant="chip-xs" className="flex items-center gap-1 bg-amber-500/10 text-amber-600 hover:bg-amber-500/20 disabled:opacity-50"
            >
              <RotateCcw className="w-3 h-3" />回滚
            </Button>
          )}
        </div>
      </div>
      <div className="flex items-center gap-2 text-xs">
        <span className="font-medium">{m.component_id}</span>
        <span className="text-muted-foreground">v{m.from_version}</span>
        <ArrowRight className="w-3 h-3 text-primary" />
        <span className="text-primary font-medium">v{m.to_version}</span>
      </div>
      {m.error && (
        <div className="text-[10px] text-red-500 bg-red-500/5 rounded p-2 border border-red-500/20">
          {m.error}
        </div>
      )}
      <div className="text-[10px] text-muted-foreground">
        {m.started_at && `开始: ${formatTime(m.started_at)}`}
        {m.completed_at && ` · 完成: ${formatTime(m.completed_at)}`}
      </div>
    </div>
  );
}

// ── Dependency Graph (simple list view) ─────────────────────

function DependencyGraphView({ graph }: { graph: DepGraph }) {
  const entries = Object.entries(graph);
  return (
    <div className="space-y-2">
      {entries.map(([id, node]) => {
        const deps = Object.entries(node.dependencies || {});
        return (
          <div key={id} className="rounded-lg border border-border bg-card p-3">
            <div className="flex items-center gap-2">
              <Package className="w-3.5 h-3.5 text-primary" />
              <span className="text-xs font-medium">{node.name}</span>
              <span className="text-[10px] text-muted-foreground font-mono">v{node.version}</span>
            </div>
            {deps.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-2 pl-5">
                {deps.map(([dep, ver]) => (
                  <Badge variant="bare" key={dep} className="gap-1 bg-blue-500/10 text-blue-500 border border-blue-500/20">
                    <ArrowUpRight className="w-2.5 h-2.5" />
                    {dep} {ver}
                  </Badge>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Schema Diff View ────────────────────────────────────────

function SchemaDiffView({ diff, onClose }: { diff: SchemaDiff; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4 max-h-[80vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between p-4 border-b border-border">
          <h2 className="text-sm font-medium flex items-center gap-2">
            <Diff className="w-4 h-4" />
            Schema Diff: {diff.component_id} v{diff.from_version} → v{diff.to_version}
          </h2>
          <Button onClick={onClose} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
        </div>
        <div className="p-4 space-y-3">
          {diff.added.length > 0 && (
            <div>
              <div className="text-xs font-medium text-green-500 mb-1">新增字段</div>
              {diff.added.map((f) => (
                <div key={f.name} className="text-xs text-green-400 bg-green-500/5 rounded px-2 py-1 mb-1">+ {f.name} ({f.field_type})</div>
              ))}
            </div>
          )}
          {diff.removed.length > 0 && (
            <div>
              <div className="text-xs font-medium text-red-500 mb-1">移除字段</div>
              {diff.removed.map((f) => (
                <div key={f} className="text-xs text-red-400 bg-red-500/5 rounded px-2 py-1 mb-1">- {f}</div>
              ))}
            </div>
          )}
          {diff.modified.length > 0 && (
            <div>
              <div className="text-xs font-medium text-amber-500 mb-1">修改字段</div>
              {diff.modified.map((f) => (
                <div key={f} className="text-xs text-amber-400 bg-amber-500/5 rounded px-2 py-1 mb-1">~ {f}</div>
              ))}
            </div>
          )}
          {diff.added.length === 0 && diff.removed.length === 0 && diff.modified.length === 0 && (
            <div className="text-xs text-muted-foreground text-center py-4">无变更</div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────

export default function HeredityPage() {
  const [components, setComponents] = useState<ComponentInfo[]>([]);
  const [migrations, setMigrations] = useState<MigrationInfo[]>([]);
  const [changelog, setChangelog] = useState<ChangelogEntry[]>([]);
  const [depGraph, setDepGraph] = useState<DepGraph>({});
  const [platform, setPlatform] = useState<PlatformInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [tab, setTab] = useState<'components' | 'migrations' | 'graph' | 'changelog'>('components');
  const [expandedComp, setExpandedComp] = useState<string | null>(null);
  const [compDetail, setCompDetail] = useState<Record<string, ComponentDetail>>({});
  const [loadingDetail, setLoadingDetail] = useState<string | null>(null);
  const [executingMigration, setExecutingMigration] = useState<string | null>(null);
  const [schemaDiff, setSchemaDiff] = useState<SchemaDiff | null>(null);
  const [showNewMigration, setShowNewMigration] = useState(false);
  const [newMigration, setNewMigration] = useState({ component_id: '', from_version: '', to_version: '', dry_run: false });
  const [creatingMigration, setCreatingMigration] = useState(false);

  const fetchAll = useCallback(async () => {
    try {
      setLoading(true);
      setError('');
      const [compData, migData, graphData, clData, platData] = await Promise.all([
        apiFetch('/api/heredity/components').catch(() => ({ components: [] })),
        apiFetch('/api/heredity/migrations').catch(() => ({ migrations: [] })),
        apiFetch('/api/heredity/dependencies').catch(() => ({ graph: {} })),
        apiFetch('/api/heredity/changelog?limit=50').catch(() => ({ changelog: [] })),
        apiFetch('/api/heredity/platform').catch(() => null),
      ]);
      setComponents(Array.isArray(compData.components) ? compData.components : []);
      setMigrations(Array.isArray(migData.migrations) ? migData.migrations : []);
      setDepGraph(graphData.graph || {});
      setChangelog(Array.isArray(clData.changelog) ? clData.changelog : []);
      if (platData) setPlatform(platData as PlatformInfo);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const toggleComp = async (id: string) => {
    if (expandedComp === id) {
      setExpandedComp(null);
      return;
    }
    setExpandedComp(id);
    if (!compDetail[id]) {
      setLoadingDetail(id);
      try {
        const data = await apiFetch(`/api/heredity/components/${id}`);
        setCompDetail((prev) => ({ ...prev, [id]: data as ComponentDetail }));
      } catch (e: any) {
        setError(e.message);
      } finally {
        setLoadingDetail(null);
      }
    }
  };

  const executeMigration = async (id: string) => {
    try {
      setExecutingMigration(id);
      await apiFetch(`/api/heredity/migrations/${id}/execute`, { method: 'POST' });
      fetchAll();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setExecutingMigration(null);
    }
  };

  const rollbackMigration = async (id: string) => {
    if (!confirm('确定要回滚此迁移？')) return;
    try {
      setExecutingMigration(id);
      await apiFetch(`/api/heredity/migrations/${id}/rollback`, { method: 'POST' });
      fetchAll();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setExecutingMigration(null);
    }
  };

  const createMigration = async () => {
    if (!newMigration.component_id || !newMigration.from_version || !newMigration.to_version) return;
    try {
      setCreatingMigration(true);
      await apiFetch('/api/heredity/migrations', {
        method: 'POST',
        body: JSON.stringify(newMigration),
      });
      setShowNewMigration(false);
      setNewMigration({ component_id: '', from_version: '', to_version: '', dry_run: false });
      fetchAll();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCreatingMigration(false);
    }
  };

  const fetchSchemaDiff = async (componentId: string, from: string, to: string) => {
    try {
      const data = await apiFetch(`/api/heredity/schemas/${componentId}/diff?from_version=${from}&to_version=${to}`);
      setSchemaDiff(data as SchemaDiff);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const filtered = components.filter((c) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return c.component_name.toLowerCase().includes(q) || c.component_id.toLowerCase().includes(q);
  });

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  const activeCount = components.filter((c) => c.status === 'active' || c.status === 'stable').length;
  const completedMigs = migrations.filter((m) => m.status === 'completed').length;
  const failedMigs = migrations.filter((m) => m.status === 'failed').length;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><Package className="w-3.5 h-3.5" />组件</div>
          <div className="text-2xl font-bold">{components.length}</div>
          <div className="text-[10px] text-green-500">{activeCount} 活跃</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><GitBranch className="w-3.5 h-3.5" />迁移</div>
          <div className="text-2xl font-bold">{migrations.length}</div>
          <div className="text-[10px] text-green-500">{completedMigs} 完成</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><Layers className="w-3.5 h-3.5" />依赖</div>
          <div className="text-2xl font-bold">{Object.keys(depGraph).length}</div>
          <div className="text-[10px] text-muted-foreground">关系节点</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><Activity className="w-3.5 h-3.5" />平台版本</div>
          <div className="text-2xl font-bold">{platform?.platform_version || '-'}</div>
          <div className="text-[10px] text-muted-foreground">{changelog.length} 变更记录</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索组件..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <Button onClick={fetchAll} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
        <Button onClick={() => setShowNewMigration(true)} variant="primary" className="gap-1.5">
          <GitBranch className="w-3.5 h-3.5" />新建迁移
        </Button>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 p-2 text-xs text-red-500 bg-red-500/5 rounded-md border border-red-500/20">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <Button variant="bare" onClick={() => setError('')}><X className="w-3 h-3" /></Button>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border">
        {([['components', '组件', Package], ['migrations', '迁移', GitBranch], ['graph', '依赖图', Layers], ['changelog', '变更日志', BookOpen]] as const).map(([key, label, Icon]) => (
          <Button
            key={key}
            onClick={() => setTab(key)}
            variant="tabline" className={` ${tab === key ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'}`}
          >
            <Icon className="w-3.5 h-3.5" />{label}
          </Button>
        ))}
      </div>

      {/* Tab content */}
      {tab === 'components' && (
        <div className="space-y-2">
          {filtered.map((comp) => (
            <ComponentCard
              key={comp.component_id}
              comp={comp}
              expanded={expandedComp === comp.component_id}
              onToggle={() => toggleComp(comp.component_id)}
              detail={compDetail[comp.component_id] || null}
              loadingDetail={loadingDetail === comp.component_id}
            />
          ))}
          {filtered.length === 0 && (
            <div className="text-center py-12 text-muted-foreground">
              <Package className="w-10 h-10 mx-auto mb-2 opacity-30" />
              <p className="text-xs">{search ? '没有匹配的组件' : '暂无注册组件'}</p>
            </div>
          )}
        </div>
      )}

      {tab === 'migrations' && (
        <div className="space-y-2">
          {migrations.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              <GitBranch className="w-10 h-10 mx-auto mb-2 opacity-30" />
              <p className="text-xs">暂无迁移记录</p>
            </div>
          ) : (
            migrations.map((m) => (
              <MigrationCard
                key={m.migration_id}
                m={m}
                executing={executingMigration === m.migration_id}
                onExecute={() => executeMigration(m.migration_id)}
                onRollback={() => rollbackMigration(m.migration_id)}
              />
            ))
          )}
        </div>
      )}

      {tab === 'graph' && (
        Object.keys(depGraph).length === 0 ? (
          <div className="text-center py-12 text-muted-foreground">
            <Layers className="w-10 h-10 mx-auto mb-2 opacity-30" />
            <p className="text-xs">暂无依赖数据</p>
          </div>
        ) : (
          <DependencyGraphView graph={depGraph} />
        )
      )}

      {tab === 'changelog' && (
        <div className="space-y-1">
          {changelog.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              <BookOpen className="w-10 h-10 mx-auto mb-2 opacity-30" />
              <p className="text-xs">暂无变更日志</p>
            </div>
          ) : (
            changelog.map((entry, i) => (
              <div key={i} className="flex items-center gap-3 p-3 rounded-lg border border-border bg-card hover:bg-muted/30 transition-colors">
                <div className="w-6 h-6 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                  <Tag className="w-3 h-3 text-primary" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium">{entry.component_id}</span>
                    <span className="text-[10px] text-muted-foreground font-mono">v{entry.version}</span>
                    <Badge variant="muted">{entry.action}</Badge>
                  </div>
                  {entry.details && <p className="text-[10px] text-muted-foreground mt-0.5 truncate">{entry.details}</p>}
                </div>
                <span className="text-[10px] text-muted-foreground shrink-0">{formatTime(entry.timestamp)}</span>
              </div>
            ))
          )}
        </div>
      )}

      {/* Schema Diff Modal */}
      {schemaDiff && <SchemaDiffView diff={schemaDiff} onClose={() => setSchemaDiff(null)} />}

      {/* New Migration Dialog */}
      {showNewMigration && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowNewMigration(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-md mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">新建迁移</h2>
              <Button onClick={() => setShowNewMigration(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">组件</label>
                <select
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  value={newMigration.component_id}
                  onChange={(e) => setNewMigration((p) => ({ ...p, component_id: e.target.value }))}
                >
                  <option value="">选择组件</option>
                  {components.map((c) => (
                    <option key={c.component_id} value={c.component_id}>{c.component_name} (v{c.version})</option>
                  ))}
                </select>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground">源版本</label>
                  <input
                    className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                    value={newMigration.from_version}
                    onChange={(e) => setNewMigration((p) => ({ ...p, from_version: e.target.value }))}
                    placeholder="0.1.0"
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">目标版本</label>
                  <input
                    className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                    value={newMigration.to_version}
                    onChange={(e) => setNewMigration((p) => ({ ...p, to_version: e.target.value }))}
                    placeholder="0.2.0"
                  />
                </div>
              </div>
              <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer">
                <input
                  type="checkbox"
                  checked={newMigration.dry_run}
                  onChange={(e) => setNewMigration((p) => ({ ...p, dry_run: e.target.checked }))}
                  className="rounded"
                />
                试运行（不实际执行）
              </label>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowNewMigration(false)} variant="secondary">取消</Button>
                <Button onClick={createMigration}
                  disabled={!newMigration.component_id || !newMigration.from_version || !newMigration.to_version || creatingMigration} variant="primary">
                  {creatingMigration ? <Loader2 className="w-3 h-3 animate-spin" /> : <GitBranch className="w-3 h-3" />}
                  创建
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
