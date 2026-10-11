'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import { Search, Bot, Download, Trash2, RefreshCw, X, CheckCircle, XCircle, Clock, Filter } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

interface Agent {
  id: string;
  name: string;
  binary: string;
  description: string;
  icon: string;
  category: string;
  available: boolean;
  version: string | null;
  path: string | null;
  installCommand: string;
  os: string;
}

interface InstallStatus {
  status: string;
  progress: number;
  line_count: number;
  error: string | null;
}

const CATEGORIES = [
  { value: 'all', label: '全部' },
  { value: 'coding', label: '编程' },
  { value: 'ide', label: 'IDE' },
  { value: 'chat', label: '聊天' },
  { value: 'automation', label: '自动化' },
  { value: 'research', label: '研究' },
  { value: 'devops', label: 'DevOps' },
  { value: 'workflow', label: '工作流' },
];

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('all');
  const [filterInstalled, setFilterInstalled] = useState<'all' | 'installed' | 'available'>('all');
  const [installing, setInstalling] = useState<Set<string>>(new Set());
  const [installStatuses, setInstallStatuses] = useState<Record<string, InstallStatus>>({});
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [showDetail, setShowDetail] = useState(false);

  const fetchAgents = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiFetch('/api/agents/detect');
      setAgents(Array.isArray(data.agents) ? data.agents : []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchInstallStatuses = useCallback(async () => {
    try {
      const data = await apiFetch('/api/agents/install/status');
      setInstallStatuses(data || {});
      // Clear installing set for completed tasks
      setInstalling((prev) => {
        const next = new Set(prev);
        for (const aid of prev) {
          const s = data[aid];
          if (s && (s.status === 'done' || s.status === 'error')) {
            next.delete(aid);
          }
        }
        return next.size === prev.size ? prev : next;
      });
    } catch {
      // silent
    }
  }, []);

  useEffect(() => {
    fetchAgents();
  }, [fetchAgents]);

  // Poll install statuses while something is installing
  useEffect(() => {
    if (installing.size === 0) return;
    const iv = setInterval(fetchInstallStatuses, 2000);
    return () => clearInterval(iv);
  }, [installing.size, fetchInstallStatuses]);

  const handleInstall = async (agentId: string) => {
    try {
      setInstalling((prev) => new Set(prev).add(agentId));
      await apiFetch('/api/agents/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent_id: agentId }),
      });
    } catch (e: any) {
      setError(e.message);
      setInstalling((prev) => {
        const next = new Set(prev);
        next.delete(agentId);
        return next;
      });
    }
  };

  const handleUninstall = async (agentId: string) => {
    if (!confirm(`确定要卸载 ${agents.find((a) => a.id === agentId)?.name || agentId}？`)) return;
    try {
      await apiFetch('/api/agents/uninstall', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent_id: agentId }),
      });
      await fetchAgents();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleUpdate = async (agentId: string) => {
    try {
      setInstalling((prev) => new Set(prev).add(agentId));
      await apiFetch('/api/agents/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent_id: agentId }),
      });
    } catch (e: any) {
      setError(e.message);
      setInstalling((prev) => {
        const next = new Set(prev);
        next.delete(agentId);
        return next;
      });
    }
  };

  const filtered = agents.filter((a) => {
    if (category !== 'all' && a.category !== category) return false;
    if (filterInstalled === 'installed' && !a.available) return false;
    if (filterInstalled === 'available' && a.available) return false;
    if (search) {
      const q = search.toLowerCase();
      return (
        a.name.toLowerCase().includes(q) ||
        a.description.toLowerCase().includes(q) ||
        a.binary.toLowerCase().includes(q)
      );
    }
    return true;
  });

  const installedCount = agents.filter((a) => a.available).length;
  const totalCount = agents.length;

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;
  if (error) return <div className="text-sm text-red-500 p-4">错误: {error}</div>;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{totalCount}</div>
          <div className="text-xs text-muted-foreground">注册Agent</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-600">{installedCount}</div>
          <div className="text-xs text-muted-foreground">已安装</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-orange-500">{totalCount - installedCount}</div>
          <div className="text-xs text-muted-foreground">可安装</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索Agent名称、描述..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={category}
          onChange={(e) => setCategory(e.target.value)}
        >
          {CATEGORIES.map((c) => (
            <option key={c.value} value={c.value}>{c.label}</option>
          ))}
        </select>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={filterInstalled}
          onChange={(e) => setFilterInstalled(e.target.value as any)}
        >
          <option value="all">全部状态</option>
          <option value="installed">已安装</option>
          <option value="available">未安装</option>
        </select>
        <Button onClick={() => fetchAgents()} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
      </div>

      {/* Agent Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {filtered.map((agent) => {
          const isInstalling = installing.has(agent.id);
          const status = installStatuses[agent.id];
          return (
            <div
              key={agent.id}
              className="rounded-lg border border-border bg-card p-4 hover:bg-muted/30 transition-colors cursor-pointer"
              onClick={() => { setSelectedAgent(agent); setShowDetail(true); }}
            >
              <div className="flex items-start gap-3">
                <span className="text-2xl">{agent.icon}</span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-medium truncate">{agent.name}</h3>
                    {agent.available ? (
                      <span className="flex items-center gap-0.5 rounded-full px-2 py-0.5 text-[10px] font-medium bg-emerald-500/10 text-emerald-500">
                        <CheckCircle className="w-2.5 h-2.5" />已安装
                      </span>
                    ) : (
                      <Badge variant="muted">未安装</Badge>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{agent.description}</p>
                  <div className="flex items-center gap-2 mt-2 text-[10px] text-muted-foreground">
                    <span className="px-1.5 py-0.5 rounded bg-muted">{agent.category}</span>
                    {agent.version && <span>v{agent.version.slice(0, 20)}</span>}
                    <span className="font-mono">{agent.binary}</span>
                  </div>
                </div>
              </div>
              {/* Action bar */}
              <div className="flex items-center gap-1.5 mt-3 pt-3 border-t border-border" onClick={(e) => e.stopPropagation()}>
                {isInstalling ? (
                  <div className="flex-1">
                    <div className="flex items-center gap-2 text-xs text-primary">
                      <Clock className="w-3.5 h-3.5 animate-spin" />
                      <span>安装中{status ? ` ${status.progress}%` : '...'}</span>
                    </div>
                    {status && (
                      <div className="w-full bg-muted rounded-full h-1 mt-1">
                        <div className="bg-primary h-1 rounded-full transition-all" style={{ width: `${status.progress}%` }} />
                      </div>
                    )}
                  </div>
                ) : (
                  <>
                    {agent.available ? (
                      <>
                        <Button
                          onClick={() => handleUpdate(agent.id)}
                          variant="chip-xs" className="flex items-center gap-1 bg-primary/10 text-primary hover:bg-primary/20"
                        >
                          <RefreshCw className="w-3 h-3" />更新
                        </Button>
                        <Button
                          onClick={() => handleUninstall(agent.id)}
                          variant="chip-xs" className="flex items-center gap-1 bg-red-500/10 text-red-500 hover:bg-red-500/20"
                        >
                          <Trash2 className="w-3 h-3" />卸载
                        </Button>
                      </>
                    ) : (
                      <Button onClick={() => handleInstall(agent.id)} variant="primary" className="py-1 text-[10px]">
                        <Download className="w-3 h-3" />安装
                      </Button>
                    )}
                  </>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {filtered.length === 0 && (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
          <Bot className="w-10 h-10 mb-2 opacity-30" />
          <p className="text-xs">没有匹配的Agent</p>
        </div>
      )}

      {/* Detail Dialog */}
      {showDetail && selectedAgent && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowDetail(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4 max-h-[80vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <div className="flex items-center gap-2">
                <span className="text-2xl">{selectedAgent.icon}</span>
                <h2 className="text-base font-medium">{selectedAgent.name}</h2>
              </div>
              <Button onClick={() => setShowDetail(false)} variant="icon-sm" className="hover:bg-muted">
                <X className="w-4 h-4" />
              </Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <div className="text-xs text-muted-foreground">描述</div>
                <div className="text-sm mt-0.5">{selectedAgent.description}</div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <div className="text-xs text-muted-foreground">分类</div>
                  <div className="text-sm mt-0.5">{selectedAgent.category}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">二进制文件</div>
                  <div className="text-sm mt-0.5 font-mono">{selectedAgent.binary}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">状态</div>
                  <div className="text-sm mt-0.5">
                    {selectedAgent.available ? (
                      <span className="text-green-600">已安装</span>
                    ) : (
                      <span className="text-muted-foreground">未安装</span>
                    )}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">版本</div>
                  <div className="text-sm mt-0.5">{selectedAgent.version || '-'}</div>
                </div>
                {selectedAgent.path && (
                  <div className="col-span-2">
                    <div className="text-xs text-muted-foreground">路径</div>
                    <div className="text-sm mt-0.5 font-mono break-all">{selectedAgent.path}</div>
                  </div>
                )}
              </div>
              <div>
                <div className="text-xs text-muted-foreground">安装命令</div>
                <pre className="text-xs bg-muted p-2 rounded mt-1 overflow-x-auto font-mono">{selectedAgent.installCommand}</pre>
              </div>
              {installStatuses[selectedAgent.id] && (
                <div>
                  <div className="text-xs text-muted-foreground">安装进度</div>
                  <div className="mt-1">
                    <div className="flex items-center gap-2 text-xs">
                      <span className={`font-medium ${installStatuses[selectedAgent.id].status === 'done' ? 'text-green-600' : installStatuses[selectedAgent.id].status === 'error' ? 'text-red-500' : 'text-primary'}`}>
                        {installStatuses[selectedAgent.id].status === 'done' ? '已完成' : installStatuses[selectedAgent.id].status === 'error' ? '失败' : '进行中'}
                      </span>
                      <span className="text-muted-foreground">{installStatuses[selectedAgent.id].progress}%</span>
                    </div>
                    <div className="w-full bg-muted rounded-full h-1.5 mt-1">
                      <div
                        className={`h-1.5 rounded-full transition-all ${installStatuses[selectedAgent.id].status === 'error' ? 'bg-red-500' : 'bg-primary'}`}
                        style={{ width: `${installStatuses[selectedAgent.id].progress}%` }}
                      />
                    </div>
                    {installStatuses[selectedAgent.id].error && (
                      <p className="text-xs text-red-500 mt-1">{installStatuses[selectedAgent.id].error}</p>
                    )}
                  </div>
                </div>
              )}
              <div className="flex items-center gap-2 pt-2">
                {selectedAgent.available ? (
                  <>
                    <Button
                      onClick={() => { handleUpdate(selectedAgent.id); }}
                      variant="chip-sm" className="flex items-center gap-1 bg-primary/10 text-primary hover:bg-primary/20"
                    >
                      <RefreshCw className="w-3.5 h-3.5" />更新
                    </Button>
                    <Button
                      onClick={() => { handleUninstall(selectedAgent.id); setShowDetail(false); }}
                      variant="chip-sm" className="flex items-center gap-1 bg-red-500/10 text-red-500 hover:bg-red-500/20"
                    >
                      <Trash2 className="w-3.5 h-3.5" />卸载
                    </Button>
                  </>
                ) : (
                  <Button onClick={() => { handleInstall(selectedAgent.id); }} variant="primary">
                    <Download className="w-3.5 h-3.5" />安装
                  </Button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
