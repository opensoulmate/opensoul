'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import { Shield, Search, Plus, Trash2, X, Users, Key, FileText, RefreshCw, CheckCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

interface Role {
  name: string;
}

interface Policy {
  role: string;
  resource: string;
  action: string;
}

interface UserRole {
  username: string;
  role: string;
}

type Tab = 'roles' | 'policies' | 'assignments';

export default function PermissionsPage() {
  const [tab, setTab] = useState<Tab>('roles');
  const [roles, setRoles] = useState<Role[]>([]);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');

  // Create role dialog
  const [showCreateRole, setShowCreateRole] = useState(false);
  const [newRoleName, setNewRoleName] = useState('');

  // Create policy dialog
  const [showCreatePolicy, setShowCreatePolicy] = useState(false);
  const [policyForm, setPolicyForm] = useState({ role: '', resource: '', action: '' });

  // Assign role dialog
  const [showAssignRole, setShowAssignRole] = useState(false);
  const [assignForm, setAssignForm] = useState({ username: '', role: '' });

  const fetchRoles = useCallback(async () => {
    try {
      const data = await apiFetch('/api/permission/roles/all');
      setRoles(Array.isArray(data) ? data : data.roles || []);
    } catch {
      // fallback: try enterprise endpoint
      try {
        const data = await apiFetch('/api/permission/policies');
        setRoles(Array.isArray(data) ? data : data.roles || []);
      } catch { /* ignore */ }
    }
  }, []);

  const fetchPolicies = useCallback(async () => {
    try {
      const data = await apiFetch('/api/permission/policies');
      setPolicies(Array.isArray(data) ? data : data.policies || []);
    } catch { /* ignore */ }
  }, []);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError('');
    await Promise.all([fetchRoles(), fetchPolicies()]);
    setLoading(false);
  }, [fetchRoles, fetchPolicies]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleCreateRole = async () => {
    if (!newRoleName.trim()) return;
    try {
      await apiFetch('/api/enterprise/roles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newRoleName.trim() }),
      });
      setShowCreateRole(false);
      setNewRoleName('');
      await fetchRoles();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleDeleteRole = async (name: string) => {
    if (!confirm(`确定删除角色 "${name}"？`)) return;
    try {
      await apiFetch('/api/permission/role', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: '', role: name }),
      });
      await fetchData();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleCreatePolicy = async () => {
    if (!policyForm.role || !policyForm.resource || !policyForm.action) return;
    try {
      await apiFetch('/api/enterprise/permissions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          role_name: policyForm.role,
          resource: policyForm.resource,
          action: policyForm.action,
        }),
      });
      setShowCreatePolicy(false);
      setPolicyForm({ role: '', resource: '', action: '' });
      await fetchPolicies();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleDeletePolicy = async (p: Policy) => {
    if (!confirm(`确定删除策略 "${p.role} → ${p.resource}:${p.action}"？`)) return;
    try {
      await apiFetch('/api/permission/policy', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role: p.role, resource: p.resource, action: p.action }),
      });
      await fetchPolicies();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleAssignRole = async () => {
    if (!assignForm.username || !assignForm.role) return;
    try {
      await apiFetch('/api/permission/role', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: assignForm.username, role: assignForm.role }),
      });
      setShowAssignRole(false);
      setAssignForm({ username: '', role: '' });
    } catch (e: any) {
      setError(e.message);
    }
  };

  const filteredRoles = roles.filter((r) =>
    !search || r.name.toLowerCase().includes(search.toLowerCase())
  );
  const filteredPolicies = policies.filter((p) =>
    !search ||
    p.role.toLowerCase().includes(search.toLowerCase()) ||
    p.resource.toLowerCase().includes(search.toLowerCase()) ||
    p.action.toLowerCase().includes(search.toLowerCase())
  );

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 dark:bg-red-950/20 dark:border-red-800 p-3 text-xs text-red-600 dark:text-red-400 flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError('')} className="p-0.5 hover:bg-red-100 dark:hover:bg-red-900/30 rounded-md"><X className="w-3.5 h-3.5" /></button>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{roles.length}</div>
          <div className="text-xs text-muted-foreground">角色数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-600">{policies.length}</div>
          <div className="text-xs text-muted-foreground">权限策略</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-600">
            {new Set(policies.map((p) => p.role)).size}
          </div>
          <div className="text-xs text-muted-foreground">已授权角色</div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-border">
        {([
          { key: 'roles', label: '角色管理', icon: Users },
          { key: 'policies', label: '权限策略', icon: Key },
          { key: 'assignments', label: '角色分配', icon: FileText },
        ] as { key: Tab; label: string; icon: any }[]).map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex items-center gap-1.5 px-3 py-2 text-xs lg:text-sm font-medium border-b-2 transition-colors ${
              tab === t.key
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            <t.icon className="w-3.5 h-3.5" />
            {t.label}
          </button>
        ))}
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <Button onClick={fetchData} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
        {tab === 'roles' && (
          <Button onClick={() => setShowCreateRole(true)} variant="primary" className="gap-1.5">
            <Plus className="w-3.5 h-3.5" />新建角色
          </Button>
        )}
        {tab === 'policies' && (
          <Button onClick={() => setShowCreatePolicy(true)} variant="primary" className="gap-1.5">
            <Plus className="w-3.5 h-3.5" />添加策略
          </Button>
        )}
        {tab === 'assignments' && (
          <Button onClick={() => setShowAssignRole(true)} variant="primary" className="gap-1.5">
            <Plus className="w-3.5 h-3.5" />分配角色
          </Button>
        )}
      </div>

      {/* Roles Tab */}
      {tab === 'roles' && (
        <div className="rounded-lg border border-border bg-card overflow-hidden">
          <table className="w-full text-xs lg:text-sm min-w-[500px]">
            <thead className="bg-muted/50">
              <tr>
                <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">角色名</th>
                <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">关联策略数</th>
                <th className="text-right px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">操作</th>
              </tr>
            </thead>
            <tbody>
              {filteredRoles.map((r) => {
                const policyCount = policies.filter((p) => p.role === r.name).length;
                return (
                  <tr key={r.name} className="border-b border-border last:border-0 hover:bg-muted/30">
                    <td className="px-2 lg:px-4 py-2.5 text-xs font-medium">
                      <span className="flex items-center gap-1.5">
                        <CheckCircle className="w-3.5 h-3.5 text-green-500" />
                        {r.name}
                      </span>
                    </td>
                    <td className="px-2 lg:px-4 py-2.5 text-xs text-muted-foreground">{policyCount} 条</td>
                    <td className="px-2 lg:px-4 py-2.5 text-right">
                      <button
                        onClick={() => handleDeleteRole(r.name)}
                        className="p-1 text-red-500 hover:bg-red-50 dark:hover:bg-red-950/20 rounded-md"
                        title="删除角色"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </td>
                  </tr>
                );
              })}
              {filteredRoles.length === 0 && (
                <tr>
                  <td colSpan={3} className="px-2 lg:px-4 py-8 text-center text-muted-foreground text-xs">
                    <Shield className="w-8 h-8 mx-auto mb-2 opacity-30" />
                    暂无角色
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Policies Tab */}
      {tab === 'policies' && (
        <div className="rounded-lg border border-border bg-card overflow-hidden">
          <table className="w-full text-xs lg:text-sm min-w-[500px]">
            <thead className="bg-muted/50">
              <tr>
                <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">角色</th>
                <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">资源</th>
                <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">操作</th>
                <th className="text-right px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">管理</th>
              </tr>
            </thead>
            <tbody>
              {filteredPolicies.map((p, i) => (
                <tr key={i} className="border-b border-border last:border-0 hover:bg-muted/30">
                  <td className="px-2 lg:px-4 py-2.5 text-xs font-medium">
                    <Badge variant="default">{p.role}</Badge>
                  </td>
                  <td className="px-2 lg:px-4 py-2.5 text-xs font-mono">{p.resource}</td>
                  <td className="px-2 lg:px-4 py-2.5 text-xs font-mono">{p.action}</td>
                  <td className="px-2 lg:px-4 py-2.5 text-right">
                    <button
                      onClick={() => handleDeletePolicy(p)}
                      className="p-1 text-red-500 hover:bg-red-50 dark:hover:bg-red-950/20 rounded-md"
                      title="删除策略"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
              {filteredPolicies.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-2 lg:px-4 py-8 text-center text-muted-foreground text-xs">
                    <Key className="w-8 h-8 mx-auto mb-2 opacity-30" />
                    暂无权限策略
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Assignments Tab */}
      {tab === 'assignments' && (
        <div className="rounded-lg border border-border bg-card p-8 flex flex-col items-center justify-center text-muted-foreground">
          <Users className="w-10 h-10 mb-3 opacity-30" />
          <p className="text-sm">角色分配</p>
          <p className="text-xs mt-1">使用"分配角色"按钮为用户分配角色，或在用户管理页面操作</p>
        </div>
      )}

      {/* Create Role Dialog */}
      {showCreateRole && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowCreateRole(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-sm mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">新建角色</h2>
              <button onClick={() => setShowCreateRole(false)} className="p-1 rounded-md hover:bg-muted"><X className="w-4 h-4" /></button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">角色名称</label>
                <input
                  className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                  placeholder="例如: editor, viewer..."
                  value={newRoleName}
                  onChange={(e) => setNewRoleName(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleCreateRole()}
                  autoFocus
                />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowCreateRole(false)} variant="secondary">取消</Button>
                <Button onClick={handleCreateRole} variant="primary">创建</Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Create Policy Dialog */}
      {showCreatePolicy && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowCreatePolicy(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-sm mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">添加权限策略</h2>
              <button onClick={() => setShowCreatePolicy(false)} className="p-1 rounded-md hover:bg-muted"><X className="w-4 h-4" /></button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">角色</label>
                <select
                  className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none"
                  value={policyForm.role}
                  onChange={(e) => setPolicyForm((f) => ({ ...f, role: e.target.value }))}
                >
                  <option value="">选择角色...</option>
                  {roles.map((r) => (
                    <option key={r.name} value={r.name}>{r.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">资源</label>
                <input
                  className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                  placeholder="例如: users, knowledge, roles..."
                  value={policyForm.resource}
                  onChange={(e) => setPolicyForm((f) => ({ ...f, resource: e.target.value }))}
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">操作</label>
                <input
                  className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                  placeholder="例如: list, create, delete, read..."
                  value={policyForm.action}
                  onChange={(e) => setPolicyForm((f) => ({ ...f, action: e.target.value }))}
                />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowCreatePolicy(false)} variant="secondary">取消</Button>
                <Button onClick={handleCreatePolicy} variant="primary">添加</Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Assign Role Dialog */}
      {showAssignRole && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowAssignRole(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-sm mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">分配角色</h2>
              <button onClick={() => setShowAssignRole(false)} className="p-1 rounded-md hover:bg-muted"><X className="w-4 h-4" /></button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">用户名</label>
                <input
                  className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                  placeholder="输入用户名..."
                  value={assignForm.username}
                  onChange={(e) => setAssignForm((f) => ({ ...f, username: e.target.value }))}
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">角色</label>
                <select
                  className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none"
                  value={assignForm.role}
                  onChange={(e) => setAssignForm((f) => ({ ...f, role: e.target.value }))}
                >
                  <option value="">选择角色...</option>
                  {roles.map((r) => (
                    <option key={r.name} value={r.name}>{r.name}</option>
                  ))}
                </select>
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowAssignRole(false)} variant="secondary">取消</Button>
                <Button onClick={handleAssignRole} variant="primary">分配</Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
