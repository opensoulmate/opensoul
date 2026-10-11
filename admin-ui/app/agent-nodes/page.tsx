'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Server, RefreshCw, X, Plus, Trash2, Activity, Brain, Search,
  Clock, CheckCircle, AlertCircle, Loader2, Send, Zap, Radio,
  Cpu, Shield, Eye, ChevronDown, ChevronRight,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

// ── Types ──────────────────────────────────────────────────────

interface AgentNode {
  id: string;
  name: string;
  agent_type: string;
  capabilities: string[];
  status: string;
  last_heartbeat: string | null;
  created_at: string;
  online: boolean;
}

interface AgentStats {
  total_agents: number;
  by_type: Record<string, number>;
}

interface RegisterForm {
  name: string;
  agent_type: string;
  capabilities: string;
  metadata: string;
}

interface ReportForm {
  agent_id: string;
  report_type: string;
  data: string;
}

interface RecallResult {
  results: any[];
  query: string;
}

type Tab = 'nodes' | 'memory' | 'register';

const TYPE_COLORS: Record<string, string> = {
  generic: 'bg-gray-500/10 text-gray-500 border-gray-500/20',
  hermes: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
  mimo: 'bg-violet-500/10 text-violet-500 border-violet-500/20',
  codex: 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20',
  copilot: 'bg-amber-500/10 text-amber-500 border-amber-500/20',
  openclaw: 'bg-cyan-500/10 text-cyan-500 border-cyan-500/20',
  soma: 'bg-pink-500/10 text-pink-500 border-pink-500/20',
};

// ── Main Page ──────────────────────────────────────────────────

export default function AgentNodesPage() {
  const [tab, setTab] = useState<Tab>('nodes');
  const [nodes, setNodes] = useState<AgentNode[]>([]);
  const [stats, setStats] = useState<AgentStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [expandedNode, setExpandedNode] = useState<string | null>(null);

  // Register form
  const [registerForm, setRegisterForm] = useState<RegisterForm>({
    name: '', agent_type: 'generic', capabilities: '', metadata: '{}',
  });
  const [registering, setRegistering] = useState(false);
  const [registerResult, setRegisterResult] = useState<any>(null);

  // Report form
  const [reportForm, setReportForm] = useState<ReportForm>({
    agent_id: '', report_type: 'observation', data: '{}',
  });
  const [showReport, setShowReport] = useState(false);
  const [reporting, setReporting] = useState(false);

  // Memory
  const [rememberTitle, setRememberTitle] = useState('');
  const [rememberContent, setRememberContent] = useState('');
  const [rememberTags, setRememberTags] = useState('');
  const [remembering, setRemembering] = useState(false);
  const [recallQuery, setRecallQuery] = useState('');
  const [recallResults, setRecallResults] = useState<RecallResult | null>(null);
  const [recalling, setRecalling] = useState(false);

  // ── Fetch ──────────────────────────────────────────────────

  const fetchNodes = useCallback(async () => {
    try {
      const params = statusFilter ? `?status=${statusFilter}` : '';
      const data = await apiFetch(`/api/agent/nodes${params}`);
      setNodes(Array.isArray(data) ? data : []);
    } catch (e: any) {
      setError(e.message);
    }
  }, [statusFilter]);

  const fetchStats = useCallback(async () => {
    try {
      const data = await apiFetch('/api/agent/stats');
      setStats(data);
    } catch { /* ignore */ }
  }, []);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError('');
    await Promise.all([fetchNodes(), fetchStats()]);
    setLoading(false);
  }, [fetchNodes, fetchStats]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // ── Actions ────────────────────────────────────────────────

  const handleRegister = async () => {
    try {
      setRegistering(true);
      setRegisterResult(null);
      let caps: string[] = [];
      try { caps = registerForm.capabilities.split(',').map(s => s.trim()).filter(Boolean); } catch {}
      let meta = {};
      try { meta = JSON.parse(registerForm.metadata); } catch {}
      const data = await apiFetch('/api/agent/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: registerForm.name,
          agent_type: registerForm.agent_type,
          capabilities: caps,
          metadata: meta,
        }),
      });
      setRegisterResult(data);
      setRegisterForm({ name: '', agent_type: 'generic', capabilities: '', metadata: '{}' });
      fetchData();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRegistering(false);
    }
  };

  const handleDelete = async (nodeId: string, name: string) => {
    if (!confirm(`确定要删除节点「${name}」？`)) return;
    try {
      await apiFetch(`/api/agent/nodes/${nodeId}`, { method: 'DELETE' });
      fetchData();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleReport = async () => {
    try {
      setReporting(true);
      let parsedData = {};
      try { parsedData = JSON.parse(reportForm.data); } catch {}
      await apiFetch('/api/agent/report', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agent_id: reportForm.agent_id,
          report_type: reportForm.report_type,
          data: parsedData,
        }),
      });
      setShowReport(false);
      setReportForm({ agent_id: '', report_type: 'observation', data: '{}' });
    } catch (e: any) {
      setError(e.message);
    } finally {
      setReporting(false);
    }
  };

  const handleRemember = async () => {
    if (!rememberTitle || !rememberContent) return;
    try {
      setRemembering(true);
      const tags = rememberTags.split(',').map(s => s.trim()).filter(Boolean);
      await apiFetch('/api/agent/remember', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: rememberTitle, content: rememberContent, tags }),
      });
      setRememberTitle('');
      setRememberContent('');
      setRememberTags('');
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRemembering(false);
    }
  };

  const handleRecall = async () => {
    if (!recallQuery) return;
    try {
      setRecalling(true);
      setRecallResults(null);
      const data = await apiFetch('/api/agent/recall', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: recallQuery, top_k: 5 }),
      });
      setRecallResults(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRecalling(false);
    }
  };

  // ── Helpers ────────────────────────────────────────────────

  const filteredNodes = nodes.filter((n) => {
    if (search) {
      const q = search.toLowerCase();
      return n.name.toLowerCase().includes(q) || n.agent_type.toLowerCase().includes(q) || n.id.toLowerCase().includes(q);
    }
    return true;
  });

  const onlineCount = nodes.filter((n) => n.online).length;
  const offlineCount = nodes.length - onlineCount;

  const timeAgo = (ts: string | null) => {
    if (!ts) return '从未';
    const diff = Date.now() - new Date(ts).getTime();
    if (diff < 60000) return '刚刚';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`;
    return `${Math.floor(diff / 86400000)}天前`;
  };

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 p-2 text-xs text-red-500 bg-red-500/5 rounded-md border border-red-500/20">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <Button variant="bare" onClick={() => setError('')}><X className="w-3 h-3" /></Button>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><Server className="w-3.5 h-3.5" />总节点</div>
          <div className="text-lg font-bold">{stats?.total_agents ?? nodes.length}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><Radio className="w-3.5 h-3.5 text-emerald-500" />在线</div>
          <div className="text-lg font-bold text-emerald-600">{onlineCount}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><AlertCircle className="w-3.5 h-3.5 text-muted-foreground" />离线</div>
          <div className="text-lg font-bold text-muted-foreground">{offlineCount}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><Cpu className="w-3.5 h-3.5" />类型数</div>
          <div className="text-lg font-bold">{Object.keys(stats?.by_type || {}).length}</div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-border">
        {([
          { key: 'nodes', label: '节点管理', icon: Server },
          { key: 'memory', label: 'Agent记忆', icon: Brain },
          { key: 'register', label: '注册节点', icon: Plus },
        ] as { key: Tab; label: string; icon: any }[]).map((t) => (
          <Button
            key={t.key}
            onClick={() => setTab(t.key)}
            variant="tabline" className={`font-medium  ${
              tab === t.key
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            <t.icon className="w-3.5 h-3.5" />
            {t.label}
          </Button>
        ))}
        <div className="flex-1" />
        <Button onClick={fetchData} variant="secondary" className="mb-1">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
      </div>

      {/* ── Nodes Tab ── */}
      {tab === 'nodes' && (
        <div className="space-y-3">
          {/* Filters */}
          <div className="flex items-center gap-2">
            <div className="relative flex-1 max-w-xs">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
              <input
                className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                placeholder="搜索节点名称、类型、ID..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <select
              className="px-2 py-1.5 text-xs bg-muted border border-border rounded-md"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value="">全部状态</option>
              <option value="active">活跃</option>
              <option value="inactive">不活跃</option>
              <option value="error">错误</option>
            </select>
            <Button
              onClick={() => { setShowReport(true); }}
              variant="chip-sm" className="flex items-center gap-1 bg-blue-500/10 text-blue-500 border border-blue-500/20 hover:bg-blue-500/20"
            >
              <Send className="w-3 h-3" />提交报告
            </Button>
          </div>

          {/* Node List */}
          {filteredNodes.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
              <Server className="w-12 h-12 mb-3 opacity-20" />
              <p className="text-sm">暂无节点</p>
              <p className="text-xs mt-1">点击「注册节点」添加第一个Agent节点</p>
            </div>
          ) : (
            <div className="space-y-2">
              {filteredNodes.map((node) => (
                <div
                  key={node.id}
                  className="rounded-lg border border-border bg-card overflow-hidden"
                >
                  <div
                    className="flex items-center gap-3 p-3 cursor-pointer hover:bg-muted/30 transition-colors"
                    onClick={() => setExpandedNode(expandedNode === node.id ? null : node.id)}
                  >
                    <div className={`w-2.5 h-2.5 rounded-full shrink-0 ${node.online ? 'bg-green-500 animate-pulse' : 'bg-gray-400'}`} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium truncate">{node.name}</span>
                        <Badge variant="bare" className={`border ${TYPE_COLORS[node.agent_type] || TYPE_COLORS.generic}`}>
                          {node.agent_type}
                        </Badge>
                        <Badge variant="bare" className={`${node.online ? 'bg-emerald-500/10 text-emerald-500' : 'bg-muted text-muted-foreground'}`}>
                          {node.online ? '在线' : '离线'}
                        </Badge>
                        {node.status !== 'active' && (
                          <Badge variant="destructive">{node.status}</Badge>
                        )}
                      </div>
                      <div className="flex items-center gap-3 mt-1 text-[10px] text-muted-foreground">
                        <span className="flex items-center gap-1"><Clock className="w-3 h-3" />心跳: {timeAgo(node.last_heartbeat)}</span>
                        <span>注册: {new Date(node.created_at).toLocaleDateString('zh-CN')}</span>
                        {node.capabilities.length > 0 && (
                          <span className="flex items-center gap-1"><Zap className="w-3 h-3" />{node.capabilities.length} 能力</span>
                        )}
                      </div>
                    </div>
                    {expandedNode === node.id ? <ChevronDown className="w-4 h-4 text-muted-foreground" /> : <ChevronRight className="w-4 h-4 text-muted-foreground" />}
                  </div>

                  {/* Expanded Details */}
                  {expandedNode === node.id && (
                    <div className="border-t border-border p-3 bg-muted/20 space-y-2">
                      <div className="grid grid-cols-2 gap-2 text-xs">
                        <div>
                          <span className="text-muted-foreground">ID: </span>
                          <span className="font-mono text-[10px]">{node.id}</span>
                        </div>
                        <div>
                          <span className="text-muted-foreground">最后心跳: </span>
                          <span>{node.last_heartbeat ? new Date(node.last_heartbeat).toLocaleString('zh-CN') : '从未'}</span>
                        </div>
                      </div>
                      {node.capabilities.length > 0 && (
                        <div>
                          <span className="text-xs text-muted-foreground">能力列表:</span>
                          <div className="flex flex-wrap gap-1 mt-1">
                            {node.capabilities.map((cap) => (
                              <Badge variant="default" key={cap}>{cap}</Badge>
                            ))}
                          </div>
                        </div>
                      )}
                      <div className="flex items-center gap-2 pt-1">
                        <Button
                          onClick={() => { setReportForm({ ...reportForm, agent_id: node.id }); setShowReport(true); }}
                          variant="chip-xs" className="flex items-center gap-1 bg-blue-500/10 text-blue-500 hover:bg-blue-500/20"
                        >
                          <Send className="w-3 h-3" />提交报告
                        </Button>
                        <Button
                          onClick={() => handleDelete(node.id, node.name)}
                          variant="chip-xs" className="flex items-center gap-1 bg-red-500/10 text-red-500 hover:bg-red-500/20"
                        >
                          <Trash2 className="w-3 h-3" />删除节点
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Type Distribution */}
          {stats?.by_type && Object.keys(stats.by_type).length > 0 && (
            <div className="rounded-lg border border-border bg-card p-4">
              <h3 className="text-xs font-medium text-muted-foreground mb-3">类型分布</h3>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                {Object.entries(stats.by_type).map(([type, count]) => (
                  <div key={type} className="flex items-center justify-between rounded border border-border p-2">
                    <Badge variant="bare" className={`border ${TYPE_COLORS[type] || TYPE_COLORS.generic}`}>{type}</Badge>
                    <span className="text-sm font-bold">{count}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Memory Tab ── */}
      {tab === 'memory' && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Remember */}
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-center gap-2 mb-3">
              <Brain className="w-4 h-4 text-violet-500" />
              <h3 className="text-sm font-medium">存储记忆</h3>
            </div>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">标题</label>
                <input
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  value={rememberTitle}
                  onChange={(e) => setRememberTitle(e.target.value)}
                  placeholder="记忆标题..."
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">内容</label>
                <textarea
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md h-24 resize-none"
                  value={rememberContent}
                  onChange={(e) => setRememberContent(e.target.value)}
                  placeholder="记忆内容..."
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">标签（逗号分隔）</label>
                <input
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  value={rememberTags}
                  onChange={(e) => setRememberTags(e.target.value)}
                  placeholder="tag1, tag2, ..."
                />
              </div>
              <Button
                onClick={handleRemember}
                disabled={remembering || !rememberTitle || !rememberContent}
                variant="chip-sm" className="flex items-center gap-1.5 bg-violet-500/10 text-violet-500 border border-violet-500/20 hover:bg-violet-500/20 disabled:opacity-50"
              >
                {remembering ? <Loader2 className="w-3 h-3 animate-spin" /> : <Brain className="w-3 h-3" />}
                {remembering ? '存储中...' : '存储记忆'}
              </Button>
            </div>
          </div>

          {/* Recall */}
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-center gap-2 mb-3">
              <Search className="w-4 h-4 text-blue-500" />
              <h3 className="text-sm font-medium">检索记忆</h3>
            </div>
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <input
                  className="flex-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  value={recallQuery}
                  onChange={(e) => setRecallQuery(e.target.value)}
                  placeholder="输入问题检索相关记忆..."
                  onKeyDown={(e) => e.key === 'Enter' && handleRecall()}
                />
                <Button onClick={handleRecall}
                  disabled={recalling || !recallQuery} variant="primary">
                  {recalling ? <Loader2 className="w-3 h-3 animate-spin" /> : <Search className="w-3 h-3" />}
                  检索
                </Button>
              </div>

              {recallResults && (
                <div className="space-y-2 max-h-[400px] overflow-auto">
                  <p className="text-[10px] text-muted-foreground">
                    查询: "{recallResults.query}" · {recallResults.results?.length ?? 0} 条结果
                  </p>
                  {(recallResults.results || []).map((item: any, i: number) => (
                    <div key={i} className="rounded border border-border p-2 text-xs">
                      <div className="flex items-center justify-between mb-1">
                        <span className="font-medium">{item.title || item.id || `结果 ${i + 1}`}</span>
                        {item.score !== undefined && (
                          <Badge variant="default">
                            {(item.score * 100).toFixed(1)}%
                          </Badge>
                        )}
                      </div>
                      <p className="text-muted-foreground text-[10px] line-clamp-3">{item.content || item.text || JSON.stringify(item).slice(0, 200)}</p>
                    </div>
                  ))}
                  {recallResults.results?.length === 0 && (
                    <div className="text-center py-6 text-muted-foreground text-xs">未找到相关记忆</div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── Register Tab ── */}
      {tab === 'register' && (
        <div className="max-w-lg">
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-center gap-2 mb-4">
              <Plus className="w-4 h-4 text-primary" />
              <h3 className="text-sm font-medium">注册新节点</h3>
              <Badge variant="warning">需要管理员权限</Badge>
            </div>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">节点名称 *</label>
                <input
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  value={registerForm.name}
                  onChange={(e) => setRegisterForm((f) => ({ ...f, name: e.target.value }))}
                  placeholder="my-agent-node"
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">类型</label>
                <select
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  value={registerForm.agent_type}
                  onChange={(e) => setRegisterForm((f) => ({ ...f, agent_type: e.target.value }))}
                >
                  <option value="generic">generic</option>
                  <option value="hermes">hermes</option>
                  <option value="mimo">mimo</option>
                  <option value="codex">codex</option>
                  <option value="copilot">copilot</option>
                  <option value="openclaw">openclaw</option>
                  <option value="soma">soma</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">能力列表（逗号分隔）</label>
                <input
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  value={registerForm.capabilities}
                  onChange={(e) => setRegisterForm((f) => ({ ...f, capabilities: e.target.value }))}
                  placeholder="chat, code, search"
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">元数据 (JSON)</label>
                <textarea
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md h-16 resize-none font-mono"
                  value={registerForm.metadata}
                  onChange={(e) => setRegisterForm((f) => ({ ...f, metadata: e.target.value }))}
                  placeholder='{"version": "1.0"}'
                />
              </div>
              <Button onClick={handleRegister}
                disabled={registering || !registerForm.name} variant="primary" className="gap-1.5">
                {registering ? <Loader2 className="w-3 h-3 animate-spin" /> : <Shield className="w-3 h-3" />}
                {registering ? '注册中...' : '注册节点'}
              </Button>
            </div>

            {/* Register Result */}
            {registerResult && (
              <div className="mt-4 p-3 rounded-md bg-emerald-500/5 border border-emerald-500/20">
                <div className="flex items-center gap-2 mb-2">
                  <CheckCircle className="w-4 h-4 text-emerald-500" />
                  <span className="text-xs font-medium text-emerald-600">注册成功</span>
                </div>
                <div className="space-y-1 text-[10px]">
                  <div><span className="text-muted-foreground">Agent ID: </span><span className="font-mono">{registerResult.agent_id}</span></div>
                  <div><span className="text-muted-foreground">名称: </span><span>{registerResult.name}</span></div>
                  <div><span className="text-muted-foreground">Token: </span><span className="font-mono bg-muted px-1 rounded">{registerResult.token}</span></div>
                  <div className="mt-2 p-2 bg-amber-500/5 border border-amber-500/20 rounded text-amber-600">
                    ⚠️ 请妥善保管 Token，它只显示一次！
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Report Dialog ── */}
      {showReport && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowReport(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-md mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">提交Agent报告</h2>
              <Button onClick={() => setShowReport(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">Agent ID *</label>
                <input
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono"
                  value={reportForm.agent_id}
                  onChange={(e) => setReportForm((f) => ({ ...f, agent_id: e.target.value }))}
                  placeholder="UUID"
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">报告类型</label>
                <select
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  value={reportForm.report_type}
                  onChange={(e) => setReportForm((f) => ({ ...f, report_type: e.target.value }))}
                >
                  <option value="observation">observation</option>
                  <option value="error">error</option>
                  <option value="metric">metric</option>
                  <option value="event">event</option>
                  <option value="task_complete">task_complete</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">数据 (JSON)</label>
                <textarea
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md h-24 resize-none font-mono"
                  value={reportForm.data}
                  onChange={(e) => setReportForm((f) => ({ ...f, data: e.target.value }))}
                  placeholder='{"message": "..."}'
                />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowReport(false)} variant="secondary">取消</Button>
                <Button onClick={handleReport}
                  disabled={reporting || !reportForm.agent_id} variant="primary">
                  {reporting ? <Loader2 className="w-3 h-3 animate-spin" /> : <Send className="w-3 h-3" />}
                  提交
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
