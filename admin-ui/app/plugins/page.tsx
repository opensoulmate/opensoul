'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Puzzle, Plus, Trash2, Power, PowerOff, Settings, X,
  RefreshCw, Package, Check, AlertCircle
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

interface Plugin {
  id: number;
  name: string;
  version: string;
  description: string;
  type: string;
  status: string;
  manifest: Record<string, any>;
  installed_at: string;
  updated_at: string;
}

export default function PluginsPage() {
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');

  // Install dialog
  const [showInstall, setShowInstall] = useState(false);
  const [installForm, setInstallForm] = useState({ name: '', version: '0.0.0', description: '', type: 'general', manifest: '{}' });
  const [installing, setInstalling] = useState(false);

  // Detail/config dialog
  const [selectedPlugin, setSelectedPlugin] = useState<Plugin | null>(null);
  const [showDetail, setShowDetail] = useState(false);
  const [configJson, setConfigJson] = useState('');
  const [saving, setSaving] = useState(false);

  const fetchPlugins = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiFetch('/api/plugins');
      setPlugins(Array.isArray(data) ? data : []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchPlugins(); }, [fetchPlugins]);

  const handleInstall = async () => {
    try {
      setInstalling(true);
      let manifest: Record<string, any>;
      try {
        manifest = JSON.parse(installForm.manifest);
      } catch {
        setError('Manifest JSON格式错误');
        setInstalling(false);
        return;
      }
      await apiFetch('/api/plugins/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: installForm.name,
          version: installForm.version,
          description: installForm.description,
          type: installForm.type,
          manifest,
        }),
      });
      setShowInstall(false);
      setInstallForm({ name: '', version: '0.0.0', description: '', type: 'general', manifest: '{}' });
      fetchPlugins();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setInstalling(false);
    }
  };

  const handleToggle = async (plugin: Plugin) => {
    const newStatus = plugin.status === 'enabled' ? 'disabled' : 'enabled';
    try {
      await apiFetch(`/api/plugins/${plugin.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
      });
      fetchPlugins();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleUninstall = async (plugin: Plugin) => {
    if (!confirm(`确定要卸载插件「${plugin.name}」？`)) return;
    try {
      await apiFetch(`/api/plugins/${plugin.id}`, { method: 'DELETE' });
      if (showDetail && selectedPlugin?.id === plugin.id) setShowDetail(false);
      fetchPlugins();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleSaveConfig = async () => {
    if (!selectedPlugin) return;
    try {
      setSaving(true);
      let config: Record<string, any>;
      try {
        config = JSON.parse(configJson);
      } catch {
        setError('配置JSON格式错误');
        setSaving(false);
        return;
      }
      await apiFetch(`/api/plugins/${selectedPlugin.id}/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config }),
      });
      fetchPlugins();
      setShowDetail(false);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const filtered = plugins.filter((p) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return p.name.toLowerCase().includes(q) || p.description.toLowerCase().includes(q) || p.type.toLowerCase().includes(q);
  });

  const enabledCount = plugins.filter((p) => p.status === 'enabled').length;

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;
  if (error && plugins.length === 0) return <div className="text-sm text-red-500 p-4">错误: {error}</div>;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{plugins.length}</div>
          <div className="text-xs text-muted-foreground">已安装插件</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-600">{enabledCount}</div>
          <div className="text-xs text-muted-foreground">已启用</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-muted-foreground">{plugins.length - enabledCount}</div>
          <div className="text-xs text-muted-foreground">已禁用</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索插件..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <Button onClick={() => fetchPlugins()} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
        <Button onClick={() => setShowInstall(true)} variant="primary" className="gap-1.5">
          <Plus className="w-3.5 h-3.5" />安装插件
        </Button>
      </div>

      {/* Error banner */}
      {error && (
        <div className="flex items-center gap-2 p-2 text-xs text-red-500 bg-red-500/5 rounded-md border border-red-500/20">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <Button variant="bare" onClick={() => setError('')}><X className="w-3 h-3" /></Button>
        </div>
      )}

      {/* Plugin list */}
      <div className="space-y-2">
        {filtered.map((plugin) => (
          <div
            key={plugin.id}
            className="rounded-lg border border-border bg-card p-4 hover:bg-muted/30 transition-colors cursor-pointer"
            onClick={() => { setSelectedPlugin(plugin); setConfigJson(JSON.stringify(plugin.manifest, null, 2)); setShowDetail(true); }}
          >
            <div className="flex items-start justify-between">
              <div className="flex items-start gap-3 min-w-0 flex-1">
                <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                  <Puzzle className="w-5 h-5 text-primary" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-medium truncate">{plugin.name}</h3>
                    <Badge variant="muted">v{plugin.version}</Badge>
                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${plugin.status === 'enabled' ? 'bg-emerald-500/10 text-emerald-500' : 'bg-muted text-muted-foreground'}`}>
                      {plugin.status === 'enabled' ? '已启用' : '已禁用'}
                    </span>
                    <Badge variant="subtle">{plugin.type}</Badge>
                  </div>
                  <p className="text-xs text-muted-foreground mt-1 line-clamp-1">{plugin.description || '暂无描述'}</p>
                  <div className="text-[10px] text-muted-foreground mt-1">安装时间: {plugin.installed_at}</div>
                </div>
              </div>
              <div className="flex items-center gap-1 shrink-0 ml-2" onClick={(e) => e.stopPropagation()}>
                <Button
                  onClick={() => handleToggle(plugin)}
                  variant="icon-md" className={` ${plugin.status === 'enabled' ? 'hover:bg-emerald-500/10' : 'hover:bg-muted'}`}
                  title={plugin.status === 'enabled' ? '禁用' : '启用'}
                >
                  {plugin.status === 'enabled' ? (
                    <Power className="w-4 h-4 text-green-600" />
                  ) : (
                    <PowerOff className="w-4 h-4 text-muted-foreground" />
                  )}
                </Button>
                <Button
                  onClick={() => handleUninstall(plugin)}
                  variant="icon-md" className="hover:bg-red-500/10"
                  title="卸载"
                >
                  <Trash2 className="w-4 h-4 text-red-500" />
                </Button>
              </div>
            </div>
          </div>
        ))}
        {filtered.length === 0 && (
          <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
            <Package className="w-10 h-10 mb-2 opacity-30" />
            <p className="text-xs">{search ? '没有匹配的插件' : '暂未安装插件'}</p>
          </div>
        )}
      </div>

      {/* Install Dialog */}
      {showInstall && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowInstall(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4 max-h-[80vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">安装插件</h2>
              <Button onClick={() => setShowInstall(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">插件名称 *</label>
                <input
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  value={installForm.name}
                  onChange={(e) => setInstallForm((f) => ({ ...f, name: e.target.value }))}
                  placeholder="my-plugin"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground">版本</label>
                  <input
                    className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                    value={installForm.version}
                    onChange={(e) => setInstallForm((f) => ({ ...f, version: e.target.value }))}
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">类型</label>
                  <select
                    className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                    value={installForm.type}
                    onChange={(e) => setInstallForm((f) => ({ ...f, type: e.target.value }))}
                  >
                    <option value="general">通用</option>
                    <option value="ui">UI</option>
                    <option value="api">API</option>
                    <option value="tool">工具</option>
                    <option value="integration">集成</option>
                  </select>
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">描述</label>
                <input
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  value={installForm.description}
                  onChange={(e) => setInstallForm((f) => ({ ...f, description: e.target.value }))}
                  placeholder="插件描述..."
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">Manifest JSON *</label>
                <textarea
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono h-32 resize-none"
                  value={installForm.manifest}
                  onChange={(e) => setInstallForm((f) => ({ ...f, manifest: e.target.value }))}
                  placeholder='{"routes": [], "sidebar": []}'
                />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowInstall(false)} variant="secondary">取消</Button>
                <Button onClick={handleInstall}
                  disabled={!installForm.name || installing} variant="primary">
                  {installing ? '安装中...' : '安装'}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Detail/Config Dialog */}
      {showDetail && selectedPlugin && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowDetail(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4 max-h-[80vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border shrink-0">
              <div className="flex items-center gap-2">
                <Puzzle className="w-5 h-5 text-primary" />
                <h2 className="text-sm font-medium">{selectedPlugin.name}</h2>
                <Badge variant="muted">v{selectedPlugin.version}</Badge>
              </div>
              <Button onClick={() => setShowDetail(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="flex-1 overflow-auto p-4 space-y-3 min-h-0">
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div><span className="text-muted-foreground">状态:</span> <span className={selectedPlugin.status === 'enabled' ? 'text-green-600' : 'text-muted-foreground'}>{selectedPlugin.status === 'enabled' ? '已启用' : '已禁用'}</span></div>
                <div><span className="text-muted-foreground">类型:</span> {selectedPlugin.type}</div>
                <div><span className="text-muted-foreground">安装时间:</span> {selectedPlugin.installed_at}</div>
                <div><span className="text-muted-foreground">更新时间:</span> {selectedPlugin.updated_at}</div>
              </div>
              {selectedPlugin.description && (
                <div>
                  <div className="text-xs text-muted-foreground">描述</div>
                  <div className="text-sm mt-0.5">{selectedPlugin.description}</div>
                </div>
              )}
              <div>
                <label className="text-xs text-muted-foreground">Manifest / 配置</label>
                <textarea
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono h-40 resize-none"
                  value={configJson}
                  onChange={(e) => setConfigJson(e.target.value)}
                />
              </div>
            </div>
            <div className="flex items-center gap-2 p-4 border-t border-border shrink-0">
              <Button
                onClick={() => handleToggle(selectedPlugin)}
                variant="chip-sm" className={`flex items-center gap-1  ${selectedPlugin.status === 'enabled' ? 'bg-orange-500/10 text-orange-600 hover:bg-amber-500/20' : 'bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20'}`}
              >
                {selectedPlugin.status === 'enabled' ? <PowerOff className="w-3 h-3" /> : <Power className="w-3 h-3" />}
                {selectedPlugin.status === 'enabled' ? '禁用' : '启用'}
              </Button>
              <Button onClick={handleSaveConfig}
                disabled={saving} variant="primary">
                <Settings className="w-3 h-3" />{saving ? '保存中...' : '保存配置'}
              </Button>
              <Button
                onClick={() => handleUninstall(selectedPlugin)}
                variant="chip-sm" className="flex items-center gap-1 bg-red-500/10 text-red-500 hover:bg-red-500/20 ml-auto"
              >
                <Trash2 className="w-3 h-3" />卸载
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
