'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Plug, Plus, Trash2, RefreshCw, X, Link, Unlink,
  Wrench, Settings, Loader2, AlertCircle, CheckCircle, Server
} from 'lucide-react';
import { Tab, TabBar } from '@/components/ui/tab';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

interface McpServer {
  id: string;
  name: string;
  url: string;
  description: string;
  transport: string;
  enabled: boolean;
  connected: boolean;
  config: Record<string, any>;
  tools: any[];
  created_at: string;
}

interface McpTool {
  name: string;
  description: string;
  server_id: string;
  server_name: string;
  input_schema?: any;
}

export default function McpPage() {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [tools, setTools] = useState<McpTool[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [tab, setTab] = useState<'servers' | 'tools'>('servers');

  // Create dialog
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState({ name: '', url: '', description: '', transport: 'stdio', config: '{}', tools: '[]' });
  const [creating, setCreating] = useState(false);

  // Detail dialog
  const [selectedServer, setSelectedServer] = useState<McpServer | null>(null);
  const [showDetail, setShowDetail] = useState(false);
  const [editConfig, setEditConfig] = useState('');
  const [saving, setSaving] = useState(false);

  const fetchServers = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiFetch('/api/mcp/servers');
      setServers(Array.isArray(data.servers) ? data.servers : []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchTools = useCallback(async () => {
    try {
      const data = await apiFetch('/api/mcp/tools');
      setTools(Array.isArray(data.tools) ? data.tools : []);
    } catch (e: any) {
      // silent
    }
  }, []);

  useEffect(() => { fetchServers(); fetchTools(); }, [fetchServers, fetchTools]);

  const handleCreate = async () => {
    if (!createForm.name || !createForm.url) { setError('名称和URL必填'); return; }
    try {
      setCreating(true);
      let config = {}, toolsList: any[] = [];
      try { config = JSON.parse(createForm.config); } catch { setError('配置JSON格式错误'); setCreating(false); return; }
      try { toolsList = JSON.parse(createForm.tools); } catch { toolsList = []; }
      await apiFetch('/api/mcp/servers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: createForm.name, url: createForm.url,
          description: createForm.description, transport: createForm.transport,
          config, tools: toolsList,
        }),
      });
      setShowCreate(false);
      setCreateForm({ name: '', url: '', description: '', transport: 'stdio', config: '{}', tools: '[]' });
      fetchServers();
      fetchTools();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCreating(false);
    }
  };

  const handleConnect = async (serverId: string) => {
    try {
      await apiFetch(`/api/mcp/servers/${serverId}/connect`, { method: 'POST' });
      fetchServers();
      fetchTools();
    } catch (e: any) { setError(e.message); }
  };

  const handleDisconnect = async (serverId: string) => {
    try {
      await apiFetch(`/api/mcp/servers/${serverId}/disconnect`, { method: 'POST' });
      fetchServers();
    } catch (e: any) { setError(e.message); }
  };

  const handleToggleEnabled = async (server: McpServer) => {
    try {
      await apiFetch(`/api/mcp/servers/${server.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !server.enabled }),
      });
      fetchServers();
    } catch (e: any) { setError(e.message); }
  };

  const handleDelete = async (serverId: string) => {
    if (!confirm('确定要删除该MCP服务器？')) return;
    try {
      await apiFetch(`/api/mcp/servers/${serverId}`, { method: 'DELETE' });
      if (showDetail && selectedServer?.id === serverId) setShowDetail(false);
      fetchServers();
      fetchTools();
    } catch (e: any) { setError(e.message); }
  };

  const handleSaveConfig = async () => {
    if (!selectedServer) return;
    try {
      setSaving(true);
      let config: Record<string, any>;
      try { config = JSON.parse(editConfig); } catch { setError('配置JSON格式错误'); setSaving(false); return; }
      await apiFetch(`/api/mcp/servers/${selectedServer.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config }),
      });
      fetchServers();
      setShowDetail(false);
    } catch (e: any) { setError(e.message); }
    finally { setSaving(false); }
  };

  const filteredServers = servers.filter((s) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return s.name.toLowerCase().includes(q) || s.url.toLowerCase().includes(q) || s.description.toLowerCase().includes(q);
  });

  const filteredTools = tools.filter((t) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return t.name.toLowerCase().includes(q) || t.description.toLowerCase().includes(q) || t.server_name.toLowerCase().includes(q);
  });

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{servers.length}</div>
          <div className="text-xs text-muted-foreground">MCP服务器</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-600">{servers.filter((s) => s.connected).length}</div>
          <div className="text-xs text-muted-foreground">已连接</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-500">{tools.length}</div>
          <div className="text-xs text-muted-foreground">可用工具</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder={tab === 'servers' ? '搜索服务器...' : '搜索工具...'}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <TabBar>
          <Tab active={tab === 'servers'} onClick={() => setTab('servers')}>服务器</Tab>
          <Tab active={tab === 'tools'} onClick={() => setTab('tools')}>工具 ({tools.length})</Tab>
        </TabBar>
        <Button onClick={() => { fetchServers(); fetchTools(); }} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
        <Button onClick={() => setShowCreate(true)} variant="primary" className="gap-1.5">
          <Plus className="w-3.5 h-3.5" />添加服务器
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

      {/* Servers tab */}
      {tab === 'servers' && (
        <div className="space-y-2">
          {filteredServers.map((srv) => (
            <div key={srv.id} className="rounded-lg border border-border bg-card p-4 hover:bg-muted/30 transition-colors">
              <div className="flex items-start justify-between">
                <div className="min-w-0 flex-1 cursor-pointer" onClick={() => { setSelectedServer(srv); setEditConfig(JSON.stringify(srv.config, null, 2)); setShowDetail(true); }}>
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-medium truncate">{srv.name}</h3>
                    {srv.connected ? (
                      <span className="flex items-center gap-0.5 rounded-full px-2 py-0.5 text-[10px] font-medium bg-emerald-500/10 text-emerald-500">
                        <CheckCircle className="w-2.5 h-2.5" />已连接
                      </span>
                    ) : (
                      <Badge variant="muted">未连接</Badge>
                    )}
                    <Badge variant="default">{srv.transport}</Badge>
                    {!srv.enabled && <Badge variant="warning">已禁用</Badge>}
                  </div>
                  <p className="text-xs text-muted-foreground mt-1 line-clamp-1">{srv.description || srv.url}</p>
                  <div className="flex items-center gap-3 mt-1 text-[10px] text-muted-foreground">
                    <span className="font-mono">{srv.url}</span>
                    <span>工具: {srv.tools?.length || 0}</span>
                  </div>
                </div>
                <div className="flex items-center gap-1 shrink-0 ml-2" onClick={(e) => e.stopPropagation()}>
                  {srv.connected ? (
                    <Button onClick={() => handleDisconnect(srv.id)} variant="icon-md" className="hover:bg-amber-500/10" title="断开">
                      <Unlink className="w-3.5 h-3.5 text-orange-500" />
                    </Button>
                  ) : (
                    <Button onClick={() => handleConnect(srv.id)} variant="icon-md" className="hover:bg-emerald-500/10" title="连接">
                      <Link className="w-3.5 h-3.5 text-green-500" />
                    </Button>
                  )}
                  <Button onClick={() => handleToggleEnabled(srv)} variant="icon-md" className={` ${srv.enabled ? 'hover:bg-amber-500/10' : 'hover:bg-emerald-500/10'}`} title={srv.enabled ? '禁用' : '启用'}>
                    {srv.enabled ? <Settings className="w-3.5 h-3.5 text-orange-500" /> : <Settings className="w-3.5 h-3.5 text-green-500" />}
                  </Button>
                  <Button onClick={() => handleDelete(srv.id)} variant="icon-md" className="hover:bg-red-500/10" title="删除">
                    <Trash2 className="w-3.5 h-3.5 text-red-500" />
                  </Button>
                </div>
              </div>
            </div>
          ))}
          {filteredServers.length === 0 && (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <Server className="w-10 h-10 mb-2 opacity-30" />
              <p className="text-xs">{search ? '没有匹配的服务器' : '暂无MCP服务器'}</p>
            </div>
          )}
        </div>
      )}

      {/* Tools tab */}
      {tab === 'tools' && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {filteredTools.map((tool, i) => (
            <div key={`${tool.server_id}-${tool.name}-${i}`} className="rounded-lg border border-border bg-card p-3 hover:bg-muted/30 transition-colors">
              <div className="flex items-start gap-2">
                <div className="w-7 h-7 rounded bg-primary/10 flex items-center justify-center shrink-0">
                  <Wrench className="w-3.5 h-3.5 text-primary" />
                </div>
                <div className="min-w-0 flex-1">
                  <h4 className="text-xs font-medium truncate">{tool.name}</h4>
                  <p className="text-[10px] text-muted-foreground mt-0.5 line-clamp-2">{tool.description || '暂无描述'}</p>
                  <Badge variant="muted" className="mt-1 inline-block">{tool.server_name}</Badge>
                </div>
              </div>
            </div>
          ))}
          {filteredTools.length === 0 && (
            <div className="col-span-full flex flex-col items-center justify-center py-12 text-muted-foreground">
              <Wrench className="w-10 h-10 mb-2 opacity-30" />
              <p className="text-xs">{search ? '没有匹配的工具' : '暂无可用工具'}</p>
            </div>
          )}
        </div>
      )}

      {/* Create Dialog */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowCreate(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4 max-h-[80vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">添加MCP服务器</h2>
              <Button onClick={() => setShowCreate(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">名称 *</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={createForm.name} onChange={(e) => setCreateForm((f) => ({ ...f, name: e.target.value }))} placeholder="my-mcp-server" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">URL *</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono" value={createForm.url} onChange={(e) => setCreateForm((f) => ({ ...f, url: e.target.value }))} placeholder="stdio://command or http://..." />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground">传输方式</label>
                  <select className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={createForm.transport} onChange={(e) => setCreateForm((f) => ({ ...f, transport: e.target.value }))}>
                    <option value="stdio">stdio</option>
                    <option value="sse">SSE</option>
                    <option value="streamable-http">Streamable HTTP</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">描述</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={createForm.description} onChange={(e) => setCreateForm((f) => ({ ...f, description: e.target.value }))} />
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">配置 JSON</label>
                <textarea className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono h-20 resize-none" value={createForm.config} onChange={(e) => setCreateForm((f) => ({ ...f, config: e.target.value }))} />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowCreate(false)} variant="secondary">取消</Button>
                <Button onClick={handleCreate} disabled={!createForm.name || !createForm.url || creating} variant="primary">
                  {creating ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
                  {creating ? '添加中...' : '添加'}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Detail Dialog */}
      {showDetail && selectedServer && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowDetail(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4 max-h-[80vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border shrink-0">
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-medium">{selectedServer.name}</h2>
                <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${selectedServer.connected ? 'bg-emerald-500/10 text-emerald-500' : 'bg-muted text-muted-foreground'}`}>
                  {selectedServer.connected ? '已连接' : '未连接'}
                </span>
              </div>
              <Button onClick={() => setShowDetail(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="flex-1 overflow-auto p-4 space-y-3 min-h-0">
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div><span className="text-muted-foreground">URL:</span> <span className="font-mono break-all">{selectedServer.url}</span></div>
                <div><span className="text-muted-foreground">传输:</span> {selectedServer.transport}</div>
                <div><span className="text-muted-foreground">工具数:</span> {selectedServer.tools?.length || 0}</div>
                <div><span className="text-muted-foreground">状态:</span> {selectedServer.enabled ? '已启用' : '已禁用'}</div>
              </div>
              {selectedServer.description && <div><div className="text-xs text-muted-foreground">描述</div><div className="text-sm mt-0.5">{selectedServer.description}</div></div>}
              {selectedServer.tools && selectedServer.tools.length > 0 && (
                <div>
                  <div className="text-xs text-muted-foreground mb-1">工具列表</div>
                  <div className="space-y-1 max-h-32 overflow-auto">
                    {selectedServer.tools.map((t: any, i: number) => (
                      <div key={i} className="flex items-center gap-2 text-xs p-1.5 rounded bg-muted/50">
                        <Wrench className="w-3 h-3 text-primary shrink-0" />
                        <span className="font-medium">{t.name}</span>
                        <span className="text-muted-foreground truncate">{t.description}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div>
                <label className="text-xs text-muted-foreground">配置</label>
                <textarea className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono h-28 resize-none" value={editConfig} onChange={(e) => setEditConfig(e.target.value)} />
              </div>
            </div>
            <div className="flex items-center gap-2 p-4 border-t border-border shrink-0">
              {selectedServer.connected ? (
                <Button onClick={() => handleDisconnect(selectedServer.id)} variant="chip-sm" className="flex items-center gap-1 bg-amber-500/10 text-amber-500 hover:bg-amber-500/20">
                  <Unlink className="w-3 h-3" />断开
                </Button>
              ) : (
                <Button onClick={() => handleConnect(selectedServer.id)} variant="chip-sm" className="flex items-center gap-1 bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20">
                  <Link className="w-3 h-3" />连接
                </Button>
              )}
              <Button onClick={handleSaveConfig} disabled={saving} variant="primary">
                <Settings className="w-3 h-3" />{saving ? '保存中...' : '保存配置'}
              </Button>
              <Button onClick={() => handleDelete(selectedServer.id)} variant="chip-sm" className="flex items-center gap-1 bg-red-500/10 text-red-500 hover:bg-red-500/20 ml-auto">
                <Trash2 className="w-3 h-3" />删除
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
