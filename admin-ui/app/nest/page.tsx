'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Plus, Trash2, RefreshCw, X, Edit3, Save, Shield,
  PauseCircle, PlayCircle, ChevronLeft, ChevronRight, Layers,
  AlertCircle, CheckCircle, Clock, Database, Globe, Lock,
  BarChart3, FileText, Settings, Eye,
} from 'lucide-react';
import { Badge, tone } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

// ── Types ──────────────────────────────────────────────────

interface Tenant {
  tenant_id: string;
  name: string;
  tier: string;
  namespace: string;
  status: string;
  owner_user_id?: string;
  description?: string;
  tags?: string[];
  config?: Record<string, any>;
  quota?: Record<string, any>;
  usage?: Record<string, any>;
  usage_percent?: Record<string, number>;
  created_at?: string;
  updated_at?: string;
}

interface Policy {
  resource_type: string;
  namespace_scoped: boolean;
  cross_tenant_allowed: boolean;
  encryption_enabled: boolean;
  audit_access: boolean;
}

interface AuditEntry {
  id: string;
  tenant_id: string;
  resource_type: string;
  resource_id: string;
  action: string;
  allowed: boolean;
  timestamp: string;
  reason?: string;
}

interface Stats {
  total_tenants?: number;
  active_tenants?: number;
  suspended_tenants?: number;
  by_tier?: Record<string, number>;
  total_policies?: number;
  total_audit_entries?: number;
}

type Tab = 'tenants' | 'policies' | 'audit';

const TIERS = [
  { value: 'all', label: '全部套餐' },
  { value: 'free', label: '免费版' },
  { value: 'basic', label: '基础版' },
  { value: 'pro', label: '专业版' },
  { value: 'enterprise', label: '企业版' },
];

const STATUS_OPTIONS = [
  { value: 'all', label: '全部状态' },
  { value: 'active', label: '活跃' },
  { value: 'suspended', label: '已暂停' },
  { value: 'deleted', label: '已删除' },
];

const TIER_COLORS: Record<string, string> = {
  free: 'bg-muted text-muted-foreground',
  basic: 'bg-blue-500/10 text-blue-600',
  pro: 'bg-purple-500/10 text-purple-600',
  enterprise: 'bg-amber-500/10 text-amber-600',
};

const STATUS_COLORS: Record<string, string> = {
  active: tone('success'),
  suspended: tone('warning'),
  deleted: tone('destructive'),
};

// ── Main Component ─────────────────────────────────────────

export default function NestPage() {
  const [tab, setTab] = useState<Tab>('tenants');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Tenants state
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [totalTenants, setTotalTenants] = useState(0);
  const [search, setSearch] = useState('');
  const [tierFilter, setTierFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [page, setPage] = useState(0);
  const limit = 20;

  // Stats
  const [stats, setStats] = useState<Stats>({});

  // Detail / Edit
  const [selectedTenant, setSelectedTenant] = useState<Tenant | null>(null);
  const [showDetail, setShowDetail] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [editMode, setEditMode] = useState(false);
  const [form, setForm] = useState({ name: '', tier: 'free', description: '', owner_user_id: '', tags: '' });

  // Policies
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [editingPolicy, setEditingPolicy] = useState<string | null>(null);
  const [policyForm, setPolicyForm] = useState({ namespace_scoped: true, cross_tenant_allowed: false, encryption_enabled: false, audit_access: true });

  // Audit
  const [auditEntries, setAuditEntries] = useState<AuditEntry[]>([]);
  const [auditTenantFilter, setAuditTenantFilter] = useState('');
  const [auditPage, setAuditPage] = useState(0);
  const auditLimit = 30;

  // ── Fetch tenants ──────────────────────────────────────

  const fetchTenants = useCallback(async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams();
      params.set('limit', String(limit));
      params.set('offset', String(page * limit));
      if (tierFilter !== 'all') params.set('tier', tierFilter);
      if (statusFilter !== 'all') params.set('status', statusFilter);
      const data = await apiFetch(`/api/nest/tenants?${params}`);
      setTenants(Array.isArray(data.tenants) ? data.tenants : []);
      setTotalTenants(data.tenants?.length || 0);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [page, tierFilter, statusFilter]);

  const fetchStats = useCallback(async () => {
    try {
      const data = await apiFetch('/api/nest/stats');
      setStats(data);
    } catch { /* silent */ }
  }, []);

  useEffect(() => { fetchTenants(); fetchStats(); }, [fetchTenants, fetchStats]);

  // ── Fetch policies ─────────────────────────────────────

  const fetchPolicies = useCallback(async () => {
    try {
      const data = await apiFetch('/api/nest/policies');
      setPolicies(Array.isArray(data.policies) ? data.policies : []);
    } catch { /* silent */ }
  }, []);

  useEffect(() => { if (tab === 'policies') fetchPolicies(); }, [tab, fetchPolicies]);

  // ── Fetch audit ────────────────────────────────────────

  const fetchAudit = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      params.set('limit', String(auditLimit));
      if (auditTenantFilter) params.set('tenant_id', auditTenantFilter);
      const data = await apiFetch(`/api/nest/audit?${params}`);
      setAuditEntries(Array.isArray(data.entries) ? data.entries : []);
    } catch { /* silent */ }
  }, [auditTenantFilter]);

  useEffect(() => { if (tab === 'audit') fetchAudit(); }, [tab, fetchAudit, auditPage]);

  // ── CRUD handlers ──────────────────────────────────────

  const handleCreate = async () => {
    try {
      const body: any = {
        name: form.name,
        tier: form.tier,
        description: form.description,
      };
      if (form.owner_user_id) body.owner_user_id = form.owner_user_id;
      if (form.tags) body.tags = form.tags.split(',').map(t => t.trim()).filter(Boolean);
      await apiFetch('/api/nest/tenants', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      setShowCreate(false);
      setForm({ name: '', tier: 'free', description: '', owner_user_id: '', tags: '' });
      fetchTenants();
      fetchStats();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleUpdate = async () => {
    if (!selectedTenant) return;
    try {
      const body: any = {};
      if (form.name) body.name = form.name;
      if (form.tier) body.tier = form.tier;
      if (form.description) body.description = form.description;
      if (form.owner_user_id) body.owner_user_id = form.owner_user_id;
      if (form.tags) body.tags = form.tags.split(',').map(t => t.trim()).filter(Boolean);
      await apiFetch(`/api/nest/tenants/${selectedTenant.tenant_id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      setEditMode(false);
      setShowDetail(false);
      fetchTenants();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleDelete = async (tenantId: string) => {
    if (!confirm('确定要删除该租户及其所有隔离资源？')) return;
    try {
      await apiFetch(`/api/nest/tenants/${tenantId}`, { method: 'DELETE' });
      setShowDetail(false);
      fetchTenants();
      fetchStats();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleSuspend = async (tenantId: string) => {
    try {
      await apiFetch(`/api/nest/tenants/${tenantId}/suspend`, { method: 'POST' });
      fetchTenants();
      if (selectedTenant?.tenant_id === tenantId) {
        setSelectedTenant(prev => prev ? { ...prev, status: 'suspended' } : null);
      }
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleReactivate = async (tenantId: string) => {
    try {
      await apiFetch(`/api/nest/tenants/${tenantId}/reactivate`, { method: 'POST' });
      fetchTenants();
      if (selectedTenant?.tenant_id === tenantId) {
        setSelectedTenant(prev => prev ? { ...prev, status: 'active' } : null);
      }
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleUpdatePolicy = async (resourceType: string) => {
    try {
      await apiFetch(`/api/nest/policies/${resourceType}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(policyForm),
      });
      setEditingPolicy(null);
      fetchPolicies();
    } catch (e: any) {
      setError(e.message);
    }
  };

  // ── Helpers ────────────────────────────────────────────

  const openDetail = async (tenant: Tenant) => {
    try {
      const data = await apiFetch(`/api/nest/tenants/${tenant.tenant_id}`);
      setSelectedTenant(data);
      setForm({
        name: data.name || '',
        tier: data.tier || 'free',
        description: data.description || '',
        owner_user_id: data.owner_user_id || '',
        tags: (data.tags || []).join(', '),
      });
      setShowDetail(true);
      setEditMode(false);
    } catch {
      setSelectedTenant(tenant);
      setShowDetail(true);
    }
  };

  const openCreate = () => {
    setForm({ name: '', tier: 'free', description: '', owner_user_id: '', tags: '' });
    setShowCreate(true);
  };

  const filteredTenants = tenants.filter(t => {
    if (search) {
      const q = search.toLowerCase();
      return (
        t.name?.toLowerCase().includes(q) ||
        t.tenant_id?.toLowerCase().includes(q) ||
        t.namespace?.toLowerCase().includes(q) ||
        t.description?.toLowerCase().includes(q)
      );
    }
    return true;
  });

  const formatTime = (iso?: string) => {
    if (!iso) return '-';
    try {
      return new Date(iso).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
    } catch { return iso; }
  };

  const totalPages = Math.ceil((totalTenants || filteredTenants.length) / limit);

  // ── Render ─────────────────────────────────────────────

  return (
    <div className="space-y-4">
      {/* Error banner */}
      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs text-red-500">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <Button onClick={() => setError('')} variant="icon-xs" className="hover:bg-red-500/10">
            <X className="w-3 h-3" />
          </Button>
        </div>
      )}

      {/* Stats row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{stats.total_tenants ?? filteredTenants.length}</div>
          <div className="text-xs text-muted-foreground">总租户数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-600">{stats.active_tenants ?? '-'}</div>
          <div className="text-xs text-muted-foreground">活跃租户</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-orange-500">{stats.suspended_tenants ?? '-'}</div>
          <div className="text-xs text-muted-foreground">已暂停</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{policies.length || stats.total_policies || '-'}</div>
          <div className="text-xs text-muted-foreground">隔离策略</div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-border">
        {([
          { key: 'tenants', label: '租户管理', icon: <Layers className="w-3.5 h-3.5" /> },
          { key: 'policies', label: '隔离策略', icon: <Shield className="w-3.5 h-3.5" /> },
          { key: 'audit', label: '访问审计', icon: <FileText className="w-3.5 h-3.5" /> },
        ] as { key: Tab; label: string; icon: React.ReactNode }[]).map((t) => (
          <Button
            key={t.key}
            onClick={() => setTab(t.key)}
            variant="tabline" className={`font-medium  ${
              tab === t.key ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            {t.icon}{t.label}
          </Button>
        ))}
      </div>

      {/* ── Tenants Tab ── */}
      {tab === 'tenants' && (
        <div className="space-y-3">
          {/* Toolbar */}
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative flex-1 min-w-[200px]">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
              <input
                className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                placeholder="搜索租户名称、ID、命名空间..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <select
              className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
              value={tierFilter}
              onChange={(e) => { setTierFilter(e.target.value); setPage(0); }}
            >
              {TIERS.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
            <select
              className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
              value={statusFilter}
              onChange={(e) => { setStatusFilter(e.target.value); setPage(0); }}
            >
              {STATUS_OPTIONS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
            <Button onClick={openCreate} variant="primary" className="gap-1.5">
              <Plus className="w-3.5 h-3.5" />新建租户
            </Button>
            <Button onClick={() => { fetchTenants(); fetchStats(); }} variant="secondary">
              <RefreshCw className="w-3.5 h-3.5" />刷新
            </Button>
          </div>

          {/* Tenant table */}
          <div className="rounded-lg border border-border bg-card overflow-hidden">
            <table className="w-full text-xs lg:text-sm min-w-[500px]">
              <thead className="bg-muted/50">
                <tr>
                  <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">租户名称</th>
                  <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground w-20">套餐</th>
                  <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground w-20">状态</th>
                  <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground w-36">命名空间</th>
                  <th className="text-right px-2 lg:px-4 py-2.5 font-medium text-muted-foreground w-32">操作</th>
                </tr>
              </thead>
              <tbody>
                {filteredTenants.map((t) => (
                  <tr
                    key={t.tenant_id}
                    className="border-b border-border last:border-0 hover:bg-muted/30 cursor-pointer"
                    onClick={() => openDetail(t)}
                  >
                    <td className="px-2 lg:px-4 py-2.5">
                      <div className="text-xs font-medium truncate max-w-[250px]">{t.name}</div>
                      <div className="text-[10px] text-muted-foreground font-mono mt-0.5">{t.tenant_id.slice(0, 12)}...</div>
                      {t.tags && t.tags.length > 0 && (
                        <div className="flex items-center gap-1 mt-1">
                          {t.tags.slice(0, 3).map((tag) => (
                            <Badge key={tag} variant="muted">{tag}</Badge>
                          ))}
                        </div>
                      )}
                    </td>
                    <td className="px-2 lg:px-4 py-2.5">
                      <Badge variant="bare" className={`${TIER_COLORS[t.tier] || TIER_COLORS.free}`}>
                        {TIERS.find(tier => tier.value === t.tier)?.label || t.tier}
                      </Badge>
                    </td>
                    <td className="px-2 lg:px-4 py-2.5">
                      <Badge variant="bare" className={`${STATUS_COLORS[t.status] || STATUS_COLORS.active}`}>
                        {STATUS_OPTIONS.find(s => s.value === t.status)?.label || t.status}
                      </Badge>
                    </td>
                    <td className="px-2 lg:px-4 py-2.5">
                      <span className="text-[10px] font-mono text-muted-foreground">{t.namespace}</span>
                    </td>
                    <td className="px-2 lg:px-4 py-2.5 text-right" onClick={(e) => e.stopPropagation()}>
                      <div className="flex items-center justify-end gap-1">
                        {t.status === 'active' ? (
                          <Button
                            onClick={() => handleSuspend(t.tenant_id)}
                            variant="icon-sm" className="hover:bg-amber-500/10"
                            title="暂停"
                          >
                            <PauseCircle className="w-3 h-3 text-orange-500" />
                          </Button>
                        ) : (
                          <Button
                            onClick={() => handleReactivate(t.tenant_id)}
                            variant="icon-sm" className="hover:bg-emerald-500/10"
                            title="恢复"
                          >
                            <PlayCircle className="w-3 h-3 text-green-600" />
                          </Button>
                        )}
                        <Button
                          onClick={() => handleDelete(t.tenant_id)}
                          variant="icon-sm" className="hover:bg-red-500/10"
                          title="删除"
                        >
                          <Trash2 className="w-3 h-3 text-red-500" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {filteredTenants.length === 0 && (
              <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
                <Layers className="w-10 h-10 mb-2 opacity-30" />
                <p className="text-xs">暂无租户数据</p>
              </div>
            )}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>第 {page + 1}/{totalPages} 页</span>
              <div className="flex items-center gap-1">
                <Button disabled={page === 0} onClick={() => setPage(p => Math.max(0, p - 1))} variant="secondary" className="p-1.5 rounded-md disabled:opacity-30">
                  <ChevronLeft className="w-3.5 h-3.5" />
                </Button>
                <Button disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)} variant="secondary" className="p-1.5 rounded-md disabled:opacity-30">
                  <ChevronRight className="w-3.5 h-3.5" />
                </Button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Policies Tab ── */}
      {tab === 'policies' && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <div className="text-xs text-muted-foreground">资源类型隔离策略 — 控制跨租户访问权限</div>
            <Button onClick={fetchPolicies} variant="secondary">
              <RefreshCw className="w-3.5 h-3.5" />刷新
            </Button>
          </div>

          <div className="rounded-lg border border-border bg-card overflow-hidden">
            <table className="w-full text-xs lg:text-sm min-w-[500px]">
              <thead className="bg-muted/50">
                <tr>
                  <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">资源类型</th>
                  <th className="text-center px-2 lg:px-4 py-2.5 font-medium text-muted-foreground w-28">命名空间隔离</th>
                  <th className="text-center px-2 lg:px-4 py-2.5 font-medium text-muted-foreground w-28">跨租户访问</th>
                  <th className="text-center px-2 lg:px-4 py-2.5 font-medium text-muted-foreground w-24">加密</th>
                  <th className="text-center px-2 lg:px-4 py-2.5 font-medium text-muted-foreground w-24">审计</th>
                  <th className="text-right px-2 lg:px-4 py-2.5 font-medium text-muted-foreground w-16">操作</th>
                </tr>
              </thead>
              <tbody>
                {policies.map((p) => {
                  const isEditing = editingPolicy === p.resource_type;
                  return (
                    <tr key={p.resource_type} className="border-b border-border last:border-0 hover:bg-muted/30">
                      <td className="px-2 lg:px-4 py-2.5">
                        <span className="text-xs font-medium font-mono">{p.resource_type}</span>
                      </td>
                      {isEditing ? (
                        <>
                          <td className="px-2 lg:px-4 py-2.5 text-center">
                            <input type="checkbox" checked={policyForm.namespace_scoped} onChange={(e) => setPolicyForm(prev => ({ ...prev, namespace_scoped: e.target.checked }))} className="w-3.5 h-3.5" />
                          </td>
                          <td className="px-2 lg:px-4 py-2.5 text-center">
                            <input type="checkbox" checked={policyForm.cross_tenant_allowed} onChange={(e) => setPolicyForm(prev => ({ ...prev, cross_tenant_allowed: e.target.checked }))} className="w-3.5 h-3.5" />
                          </td>
                          <td className="px-2 lg:px-4 py-2.5 text-center">
                            <input type="checkbox" checked={policyForm.encryption_enabled} onChange={(e) => setPolicyForm(prev => ({ ...prev, encryption_enabled: e.target.checked }))} className="w-3.5 h-3.5" />
                          </td>
                          <td className="px-2 lg:px-4 py-2.5 text-center">
                            <input type="checkbox" checked={policyForm.audit_access} onChange={(e) => setPolicyForm(prev => ({ ...prev, audit_access: e.target.checked }))} className="w-3.5 h-3.5" />
                          </td>
                          <td className="px-2 lg:px-4 py-2.5 text-right">
                            <Button onClick={() => handleUpdatePolicy(p.resource_type)} variant="icon-sm" className="hover:bg-primary/10" title="保存">
                              <Save className="w-3 h-3 text-primary" />
                            </Button>
                            <Button onClick={() => setEditingPolicy(null)} variant="icon-sm" className="hover:bg-muted ml-1" title="取消">
                              <X className="w-3 h-3 text-muted-foreground" />
                            </Button>
                          </td>
                        </>
                      ) : (
                        <>
                          <td className="px-2 lg:px-4 py-2.5 text-center">
                            {p.namespace_scoped ? <CheckCircle className="w-3.5 h-3.5 text-green-600 mx-auto" /> : <X className="w-3.5 h-3.5 text-muted-foreground mx-auto" />}
                          </td>
                          <td className="px-2 lg:px-4 py-2.5 text-center">
                            {p.cross_tenant_allowed ? <CheckCircle className="w-3.5 h-3.5 text-green-600 mx-auto" /> : <X className="w-3.5 h-3.5 text-muted-foreground mx-auto" />}
                          </td>
                          <td className="px-2 lg:px-4 py-2.5 text-center">
                            {p.encryption_enabled ? <Lock className="w-3.5 h-3.5 text-green-600 mx-auto" /> : <X className="w-3.5 h-3.5 text-muted-foreground mx-auto" />}
                          </td>
                          <td className="px-2 lg:px-4 py-2.5 text-center">
                            {p.audit_access ? <CheckCircle className="w-3.5 h-3.5 text-green-600 mx-auto" /> : <X className="w-3.5 h-3.5 text-muted-foreground mx-auto" />}
                          </td>
                          <td className="px-2 lg:px-4 py-2.5 text-right">
                            <Button
                              onClick={() => {
                                setEditingPolicy(p.resource_type);
                                setPolicyForm({
                                  namespace_scoped: p.namespace_scoped,
                                  cross_tenant_allowed: p.cross_tenant_allowed,
                                  encryption_enabled: p.encryption_enabled,
                                  audit_access: p.audit_access,
                                });
                              }}
                              variant="icon-sm" className="hover:bg-muted"
                              title="编辑"
                            >
                              <Edit3 className="w-3 h-3 text-muted-foreground" />
                            </Button>
                          </td>
                        </>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {policies.length === 0 && (
              <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
                <Shield className="w-10 h-10 mb-2 opacity-30" />
                <p className="text-xs">暂无隔离策略</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Audit Tab ── */}
      {tab === 'audit' && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <div className="relative flex-1 min-w-[200px]">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
              <input
                className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                placeholder="按租户ID筛选..."
                value={auditTenantFilter}
                onChange={(e) => setAuditTenantFilter(e.target.value)}
              />
            </div>
            <Button onClick={fetchAudit} variant="secondary">
              <RefreshCw className="w-3.5 h-3.5" />刷新
            </Button>
          </div>

          <div className="rounded-lg border border-border bg-card overflow-hidden">
            <table className="w-full text-xs lg:text-sm min-w-[500px]">
              <thead className="bg-muted/50">
                <tr>
                  <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground w-32">时间</th>
                  <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">租户ID</th>
                  <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground w-24">资源类型</th>
                  <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground w-24">操作</th>
                  <th className="text-center px-2 lg:px-4 py-2.5 font-medium text-muted-foreground w-20">结果</th>
                </tr>
              </thead>
              <tbody>
                {auditEntries.map((e, i) => (
                  <tr key={e.id || i} className="border-b border-border last:border-0 hover:bg-muted/30">
                    <td className="px-2 lg:px-4 py-2.5 text-xs text-muted-foreground">{formatTime(e.timestamp)}</td>
                    <td className="px-2 lg:px-4 py-2.5">
                      <span className="text-[10px] font-mono">{e.tenant_id?.slice(0, 16)}...</span>
                    </td>
                    <td className="px-2 lg:px-4 py-2.5 text-xs">{e.resource_type}</td>
                    <td className="px-2 lg:px-4 py-2.5 text-xs">{e.action}</td>
                    <td className="px-2 lg:px-4 py-2.5 text-center">
                      {e.allowed ? (
                        <Badge variant="success">允许</Badge>
                      ) : (
                        <Badge variant="destructive">拒绝</Badge>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {auditEntries.length === 0 && (
              <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
                <FileText className="w-10 h-10 mb-2 opacity-30" />
                <p className="text-xs">暂无审计记录</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Create Dialog ── */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowCreate(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-md mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">新建租户</h2>
              <Button onClick={() => setShowCreate(false)} variant="icon-sm" className="hover:bg-muted">
                <X className="w-4 h-4" />
              </Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground block mb-1">租户名称 *</label>
                <input className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={form.name} onChange={(e) => setForm(prev => ({ ...prev, name: e.target.value }))} placeholder="输入租户名称" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground block mb-1">套餐类型</label>
                <select className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none" value={form.tier} onChange={(e) => setForm(prev => ({ ...prev, tier: e.target.value }))}>
                  {TIERS.filter(t => t.value !== 'all').map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground block mb-1">描述</label>
                <input className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={form.description} onChange={(e) => setForm(prev => ({ ...prev, description: e.target.value }))} placeholder="可选" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground block mb-1">所有者用户ID</label>
                <input className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={form.owner_user_id} onChange={(e) => setForm(prev => ({ ...prev, owner_user_id: e.target.value }))} placeholder="可选" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground block mb-1">标签（逗号分隔）</label>
                <input className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={form.tags} onChange={(e) => setForm(prev => ({ ...prev, tags: e.target.value }))} placeholder="tag1, tag2" />
              </div>
            </div>
            <div className="flex items-center justify-end gap-2 p-4 border-t border-border">
              <Button onClick={() => setShowCreate(false)} variant="secondary">取消</Button>
              <Button onClick={handleCreate} disabled={!form.name.trim()} variant="primary">创建</Button>
            </div>
          </div>
        </div>
      )}

      {/* ── Detail Dialog ── */}
      {showDetail && selectedTenant && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => { setShowDetail(false); setEditMode(false); }}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4 max-h-[85vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            {/* Header */}
            <div className="flex items-center justify-between p-4 border-b border-border shrink-0">
              <div className="min-w-0 flex-1">
                {editMode ? (
                  <input className="w-full px-2 py-0.5 text-sm font-medium bg-muted border border-border rounded" value={form.name} onChange={(e) => setForm(prev => ({ ...prev, name: e.target.value }))} />
                ) : (
                  <h2 className="text-sm font-medium truncate">{selectedTenant.name}</h2>
                )}
                <div className="flex items-center gap-3 mt-1 text-[10px] text-muted-foreground">
                  <span className="font-mono">{selectedTenant.tenant_id}</span>
                  <span className={`px-1.5 py-0.5 rounded-full ${STATUS_COLORS[selectedTenant.status]}`}>{STATUS_OPTIONS.find(s => s.value === selectedTenant.status)?.label || selectedTenant.status}</span>
                  <span className={`px-1.5 py-0.5 rounded-full ${TIER_COLORS[selectedTenant.tier]}`}>{TIERS.find(t => t.value === selectedTenant.tier)?.label || selectedTenant.tier}</span>
                </div>
              </div>
              <Button onClick={() => { setShowDetail(false); setEditMode(false); }} variant="icon-sm" className="hover:bg-muted shrink-0 ml-2">
                <X className="w-4 h-4" />
              </Button>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-auto p-4 space-y-4 min-h-0">
              {/* Basic info */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <div className="text-xs text-muted-foreground">命名空间</div>
                  <div className="text-xs font-mono mt-0.5">{selectedTenant.namespace}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">所有者</div>
                  <div className="text-xs mt-0.5">{selectedTenant.owner_user_id || '-'}</div>
                </div>
                {selectedTenant.description && (
                  <div className="col-span-2">
                    <div className="text-xs text-muted-foreground">描述</div>
                    <div className="text-xs mt-0.5">{selectedTenant.description}</div>
                  </div>
                )}
              </div>

              {/* Quota / Usage */}
              {selectedTenant.quota && (
                <div>
                  <div className="text-xs font-medium mb-2">资源配额与使用</div>
                  <div className="space-y-2">
                    {Object.entries(selectedTenant.quota).map(([key, limit]) => {
                      if (typeof limit !== 'number' || limit <= 0) return null;
                      const used = selectedTenant.usage?.[key] ?? 0;
                      const pct = Math.min(100, Math.round((used / limit) * 100));
                      return (
                        <div key={key}>
                          <div className="flex items-center justify-between text-[10px] mb-0.5">
                            <span className="text-muted-foreground capitalize">{key.replace(/_/g, ' ')}</span>
                            <span>{used.toLocaleString()} / {limit.toLocaleString()} ({pct}%)</span>
                          </div>
                          <div className="w-full bg-muted rounded-full h-1.5">
                            <div className={`h-1.5 rounded-full transition-all ${pct > 90 ? 'bg-red-500' : pct > 70 ? 'bg-orange-500' : 'bg-primary'}`} style={{ width: `${pct}%` }} />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Tags */}
              {selectedTenant.tags && selectedTenant.tags.length > 0 && (
                <div>
                  <div className="text-xs text-muted-foreground mb-1">标签</div>
                  <div className="flex items-center gap-1 flex-wrap">
                    {selectedTenant.tags.map((tag) => (
                      <Badge variant="default" key={tag}>{tag}</Badge>
                    ))}
                  </div>
                </div>
              )}

              {/* Edit fields */}
              {editMode && (
                <div className="space-y-3 pt-2 border-t border-border">
                  <div>
                    <label className="text-xs text-muted-foreground block mb-1">套餐类型</label>
                    <select className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none" value={form.tier} onChange={(e) => setForm(prev => ({ ...prev, tier: e.target.value }))}>
                      {TIERS.filter(t => t.value !== 'all').map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground block mb-1">描述</label>
                    <input className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={form.description} onChange={(e) => setForm(prev => ({ ...prev, description: e.target.value }))} />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground block mb-1">标签（逗号分隔）</label>
                    <input className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={form.tags} onChange={(e) => setForm(prev => ({ ...prev, tags: e.target.value }))} />
                  </div>
                </div>
              )}
            </div>

            {/* Footer actions */}
            <div className="flex items-center gap-2 p-4 border-t border-border shrink-0">
              {editMode ? (
                <>
                  <Button onClick={handleUpdate} variant="primary">
                    <Save className="w-3 h-3" />保存
                  </Button>
                  <Button onClick={() => setEditMode(false)} variant="secondary">取消</Button>
                </>
              ) : (
                <>
                  <Button onClick={() => setEditMode(true)} variant="secondary" className="gap-1">
                    <Edit3 className="w-3 h-3" />编辑
                  </Button>
                  {selectedTenant.status === 'active' ? (
                    <Button onClick={() => handleSuspend(selectedTenant.tenant_id)} variant="chip-sm" className="flex items-center gap-1 bg-amber-500/10 text-amber-500 hover:bg-amber-500/20">
                      <PauseCircle className="w-3 h-3" />暂停
                    </Button>
                  ) : (
                    <Button onClick={() => handleReactivate(selectedTenant.tenant_id)} variant="chip-sm" className="flex items-center gap-1 bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20">
                      <PlayCircle className="w-3 h-3" />恢复
                    </Button>
                  )}
                  <Button onClick={() => handleDelete(selectedTenant.tenant_id)} variant="chip-sm" className="flex items-center gap-1 bg-red-500/10 text-red-500 hover:bg-red-500/20 ml-auto">
                    <Trash2 className="w-3 h-3" />删除
                  </Button>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
