'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import { Search, UserPlus, Trash2, X, Shield, RefreshCw, Users as UsersIcon, UserCheck, UserX, Edit2, Save } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

interface User {
  id: number;
  username: string;
  is_active: boolean;
  created_at: string;
  roles: string[];
}

export default function UsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [filterRole, setFilterRole] = useState('all');
  const [allRoles, setAllRoles] = useState<string[]>([]);

  // Create dialog
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState({ username: '', password: '' });
  const [creating, setCreating] = useState(false);

  // Edit roles dialog
  const [editUser, setEditUser] = useState<User | null>(null);
  const [newRole, setNewRole] = useState('');

  const fetchUsers = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiFetch('/api/enterprise/users/list');
      const list = Array.isArray(data) ? data : data.users || [];
      setUsers(list);
      // Collect all unique roles
      const roles = new Set<string>();
      list.forEach((u: User) => u.roles?.forEach((r) => roles.add(r)));
      setAllRoles(Array.from(roles).sort());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchUsers(); }, [fetchUsers]);

  const handleCreate = async () => {
    if (!createForm.username || !createForm.password) return;
    setCreating(true);
    try {
      await apiFetch('/api/enterprise/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(createForm),
      });
      setShowCreate(false);
      setCreateForm({ username: '', password: '' });
      await fetchUsers();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCreating(false);
    }
  };

  const handleAssignRole = async () => {
    if (!editUser || !newRole.trim()) return;
    try {
      await apiFetch(`/api/enterprise/users/${editUser.id}/roles`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newRole.trim() }),
      });
      setNewRole('');
      await fetchUsers();
      // Refresh edit user
      setEditUser((prev) => {
        if (!prev) return null;
        const updated = users.find((u) => u.id === prev.id);
        return updated || prev;
      });
    } catch (e: any) {
      setError(e.message);
    }
  };

  const filtered = users.filter((u) => {
    if (filterRole !== 'all' && !u.roles?.includes(filterRole)) return false;
    if (search) {
      const q = search.toLowerCase();
      return u.username.toLowerCase().includes(q) || u.roles?.some((r) => r.toLowerCase().includes(q));
    }
    return true;
  });

  const activeCount = users.filter((u) => u.is_active).length;

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
          <div className="text-2xl font-bold">{users.length}</div>
          <div className="text-xs text-muted-foreground">总用户数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-600">{activeCount}</div>
          <div className="text-xs text-muted-foreground">活跃用户</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-600">{allRoles.length}</div>
          <div className="text-xs text-muted-foreground">角色类型</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索用户名或角色..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={filterRole}
          onChange={(e) => setFilterRole(e.target.value)}
        >
          <option value="all">全部角色</option>
          {allRoles.map((r) => (
            <option key={r} value={r}>{r}</option>
          ))}
        </select>
        <Button onClick={fetchUsers} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
        <Button onClick={() => setShowCreate(true)} variant="primary" className="gap-1.5">
          <UserPlus className="w-3.5 h-3.5" />添加用户
        </Button>
      </div>

      {/* Users Table */}
      <div className="rounded-lg border border-border bg-card overflow-hidden">
        <table className="w-full text-xs lg:text-sm min-w-[500px]">
          <thead className="bg-muted/50">
            <tr>
              <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">ID</th>
              <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">用户名</th>
              <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">状态</th>
              <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">角色</th>
              <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">创建时间</th>
              <th className="text-right px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">操作</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((u) => (
              <tr key={u.id} className="border-b border-border last:border-0 hover:bg-muted/30">
                <td className="px-2 lg:px-4 py-2.5 text-xs text-muted-foreground">{u.id}</td>
                <td className="px-2 lg:px-4 py-2.5 text-xs font-medium">{u.username}</td>
                <td className="px-2 lg:px-4 py-2.5 text-xs">
                  {u.is_active ? (
                    <span className="flex items-center gap-1 text-green-600">
                      <UserCheck className="w-3 h-3" />活跃
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-muted-foreground">
                      <UserX className="w-3 h-3" />禁用
                    </span>
                  )}
                </td>
                <td className="px-2 lg:px-4 py-2.5 text-xs">
                  <div className="flex flex-wrap gap-1">
                    {u.roles?.length > 0 ? u.roles.map((r) => (
                      <Badge variant="default" key={r}>{r}</Badge>
                    )) : <span className="text-muted-foreground text-[10px]">无角色</span>}
                  </div>
                </td>
                <td className="px-2 lg:px-4 py-2.5 text-xs text-muted-foreground">{u.created_at}</td>
                <td className="px-2 lg:px-4 py-2.5 text-right">
                  <button
                    onClick={() => setEditUser(u)}
                    className="p-1 text-muted-foreground hover:text-foreground hover:bg-muted rounded-md"
                    title="管理角色"
                  >
                    <Shield className="w-3.5 h-3.5" />
                  </button>
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={6} className="px-2 lg:px-4 py-8 text-center text-muted-foreground text-xs">
                  <UsersIcon className="w-8 h-8 mx-auto mb-2 opacity-30" />
                  暂无用户
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Create User Dialog */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowCreate(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-sm mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">添加用户</h2>
              <button onClick={() => setShowCreate(false)} className="p-1 rounded-md hover:bg-muted"><X className="w-4 h-4" /></button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">用户名</label>
                <input
                  className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                  placeholder="输入用户名..."
                  value={createForm.username}
                  onChange={(e) => setCreateForm((f) => ({ ...f, username: e.target.value }))}
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">密码</label>
                <input
                  type="password"
                  className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                  placeholder="输入密码..."
                  value={createForm.password}
                  onChange={(e) => setCreateForm((f) => ({ ...f, password: e.target.value }))}
                  onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
                />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowCreate(false)} variant="secondary">取消</Button>
                <Button onClick={handleCreate}
                  disabled={creating} variant="primary">
                  {creating ? '创建中...' : '创建'}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Edit Roles Dialog */}
      {editUser && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setEditUser(null)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-md mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">管理角色 — {editUser.username}</h2>
              <button onClick={() => setEditUser(null)} className="p-1 rounded-md hover:bg-muted"><X className="w-4 h-4" /></button>
            </div>
            <div className="p-4 space-y-4">
              <div>
                <label className="text-xs text-muted-foreground mb-2 block">当前角色</label>
                <div className="flex flex-wrap gap-1.5">
                  {editUser.roles?.length > 0 ? editUser.roles.map((r) => (
                    <span key={r} className="flex items-center gap-1 px-2 py-1 rounded-full bg-primary/10 text-primary text-xs">
                      <Shield className="w-3 h-3" />{r}
                    </span>
                  )) : <span className="text-xs text-muted-foreground">暂无角色</span>}
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">分配新角色</label>
                <div className="flex gap-2">
                  <input
                    className="flex-1 px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                    placeholder="输入角色名..."
                    value={newRole}
                    onChange={(e) => setNewRole(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleAssignRole()}
                  />
                  <Button onClick={handleAssignRole} variant="primary">
                    <Save className="w-3.5 h-3.5" />分配
                  </Button>
                </div>
              </div>
              <div className="flex justify-end pt-2">
                <Button onClick={() => setEditUser(null)} variant="secondary">关闭</Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
