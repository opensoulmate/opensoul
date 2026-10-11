'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  RefreshCw, X, Zap, Radio, Globe, Server, Plus, Trash2,
  Send, Wifi, WifiOff, Clock, Filter, Activity, Users, Eye,
  AlertCircle, Loader2, ChevronDown, ChevronRight, Unplug,
} from 'lucide-react';
import { Tab, TabBar } from '@/components/ui/tab';
import { Button } from '@/components/ui/button';
import { Badge, toneBordered } from '@/components/ui/badge';

// ── Types ───────────────────────────────────────────────────

interface NerveEvent {
  id: string;
  topic: string;
  data: Record<string, any>;
  source: string;
  timestamp: string;
  delivered_to: string[];
}

interface Subscription {
  id: string;
  topic_pattern: string;
  callback_url: string;
  created_at: string;
  delivery_count: number;
  last_delivery: string | null;
}

interface Node {
  node_id: string;
  node_type: string;
  status: string;
  metadata: Record<string, any>;
  registered_at: string;
  last_heartbeat: string;
  event_count: number;
}

interface NerveStats {
  total_events: number;
  total_nodes: number;
  online_nodes: number;
  total_subscriptions: number;
  topics: string[];
  active_streams: number;
}

// ── Helpers ─────────────────────────────────────────────────

function formatTime(ts: string) {
  if (!ts) return '-';
  try {
    return new Date(ts).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  } catch { return ts; }
}

function formatRelative(ts: string) {
  if (!ts) return '';
  try {
    const diff = Date.now() - new Date(ts).getTime();
    if (diff < 60000) return '刚刚';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`;
    return `${Math.floor(diff / 86400000)}天前`;
  } catch { return ''; }
}

function topicColor(topic: string) {
  if (topic.startsWith('soma.')) return toneBordered('info');
  if (topic.startsWith('organ.')) return toneBordered('success');
  if (topic.startsWith('system.')) return toneBordered('accent');
  if (topic.startsWith('nerve.')) return toneBordered('warning');
  return toneBordered('muted');
}

// ── Main Page ───────────────────────────────────────────────

export default function NervePage() {
  const [stats, setStats] = useState<NerveStats | null>(null);
  const [events, setEvents] = useState<NerveEvent[]>([]);
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
  const [nodes, setNodes] = useState<Node[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<'events' | 'subscriptions' | 'nodes'>('events');

  // Filters
  const [topicFilter, setTopicFilter] = useState('');
  const [expandedEvent, setExpandedEvent] = useState<string | null>(null);

  // Publish dialog
  const [showPublish, setShowPublish] = useState(false);
  const [publishForm, setPublishForm] = useState({ topic: '', data: '{}', source: '' });
  const [publishing, setPublishing] = useState(false);

  // Subscribe dialog
  const [showSubscribe, setShowSubscribe] = useState(false);
  const [subscribeForm, setSubscribeForm] = useState({ subscriber_id: '', topic_pattern: '', callback_url: '' });

  // Register node dialog
  const [showRegisterNode, setShowRegisterNode] = useState(false);
  const [nodeForm, setNodeForm] = useState({ node_id: '', node_type: 'soma', metadata: '{}' });

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError('');
    const results = await Promise.allSettled([
      apiFetch('/api/nerve/stats'),
      apiFetch(`/api/nerve/events?limit=50${topicFilter ? `&topic=${encodeURIComponent(topicFilter)}` : ''}`),
      apiFetch('/api/nerve/subscriptions'),
      apiFetch('/api/nerve/nodes'),
    ]);
    const [sRes, eRes, subRes, nRes] = results;
    if (sRes.status === 'fulfilled') setStats(sRes.value);
    if (eRes.status === 'fulfilled') setEvents(eRes.value.events || []);
    if (subRes.status === 'fulfilled') setSubscriptions(subRes.value.subscriptions || []);
    if (nRes.status === 'fulfilled') setNodes(nRes.value.nodes || []);
    if (results.every(r => r.status === 'rejected')) setError('无法连接到 OpenNerve 服务');
    setLoading(false);
  }, [topicFilter]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // ── Actions ───────────────────────────────────────────────

  const handlePublish = async () => {
    try {
      setPublishing(true);
      let data = {};
      try { data = JSON.parse(publishForm.data); } catch { data = {}; }
      await apiFetch('/api/nerve/publish', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ topic: publishForm.topic, data, source: publishForm.source }),
      });
      setShowPublish(false);
      setPublishForm({ topic: '', data: '{}', source: '' });
      fetchData();
    } catch (e: any) { setError(e.message); }
    finally { setPublishing(false); }
  };

  const handleSubscribe = async () => {
    if (!subscribeForm.subscriber_id || !subscribeForm.topic_pattern) { setError('订阅ID和主题模式必填'); return; }
    try {
      await apiFetch('/api/nerve/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(subscribeForm),
      });
      setShowSubscribe(false);
      setSubscribeForm({ subscriber_id: '', topic_pattern: '', callback_url: '' });
      fetchData();
    } catch (e: any) { setError(e.message); }
  };

  const handleUnsubscribe = async (subscriberId: string) => {
    try {
      await apiFetch(`/api/nerve/subscribe/${subscriberId}`, { method: 'DELETE' });
      fetchData();
    } catch (e: any) { setError(e.message); }
  };

  const handleRegisterNode = async () => {
    if (!nodeForm.node_id) { setError('节点ID必填'); return; }
    try {
      let metadata = {};
      try { metadata = JSON.parse(nodeForm.metadata); } catch { metadata = {}; }
      await apiFetch('/api/nerve/nodes/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ node_id: nodeForm.node_id, node_type: nodeForm.node_type, metadata }),
      });
      setShowRegisterNode(false);
      setNodeForm({ node_id: '', node_type: 'soma', metadata: '{}' });
      fetchData();
    } catch (e: any) { setError(e.message); }
  };

  const handleRemoveNode = async (nodeId: string) => {
    if (!confirm(`确定要移除节点「${nodeId}」？`)) return;
    try {
      await apiFetch(`/api/nerve/nodes/${nodeId}`, { method: 'DELETE' });
      fetchData();
    } catch (e: any) { setError(e.message); }
  };

  const handleHeartbeat = async (nodeId: string) => {
    try {
      await apiFetch('/api/nerve/nodes/heartbeat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ node_id: nodeId }),
      });
      fetchData();
    } catch (e: any) { setError(e.message); }
  };

  // ── Render ────────────────────────────────────────────────

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* ── Stats ── */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <Zap className="w-3.5 h-3.5 text-amber-500" />
            <span className="text-xs text-muted-foreground">总事件</span>
          </div>
          <div className="text-2xl font-bold">{stats?.total_events || 0}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <Server className="w-3.5 h-3.5 text-blue-500" />
            <span className="text-xs text-muted-foreground">节点</span>
          </div>
          <div className="text-2xl font-bold text-green-600">{stats?.online_nodes || 0}<span className="text-sm text-muted-foreground">/{stats?.total_nodes || 0}</span></div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <Users className="w-3.5 h-3.5 text-purple-500" />
            <span className="text-xs text-muted-foreground">订阅</span>
          </div>
          <div className="text-2xl font-bold">{stats?.total_subscriptions || 0}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <Radio className="w-3.5 h-3.5 text-green-500" />
            <span className="text-xs text-muted-foreground">SSE流</span>
          </div>
          <div className="text-2xl font-bold">{stats?.active_streams || 0}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <Globe className="w-3.5 h-3.5 text-cyan-500" />
            <span className="text-xs text-muted-foreground">主题</span>
          </div>
          <div className="text-2xl font-bold">{stats?.topics?.length || 0}</div>
        </div>
      </div>

      {/* ── Error ── */}
      {error && (
        <div className="flex items-center gap-2 p-2 text-xs text-red-500 bg-red-500/5 rounded-md border border-red-500/20">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <Button variant="bare" onClick={() => setError('')}><X className="w-3 h-3" /></Button>
        </div>
      )}

      {/* ── Toolbar ── */}
      <div className="flex flex-wrap items-center gap-2">
        <TabBar>
          <Tab active={tab === 'events'} onClick={() => setTab('events')}>事件</Tab>
          <Tab active={tab === 'subscriptions'} onClick={() => setTab('subscriptions')}>订阅 ({subscriptions.length})</Tab>
          <Tab active={tab === 'nodes'} onClick={() => setTab('nodes')}>节点 ({nodes.length})</Tab>
        </TabBar>
        {tab === 'events' && (
          <div className="relative flex-1 min-w-[200px]">
            <Filter className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
            <input
              className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
              placeholder="按主题过滤 (如 soma.*)..."
              value={topicFilter}
              onChange={(e) => setTopicFilter(e.target.value)}
            />
          </div>
        )}
        <Button onClick={fetchData} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
        {tab === 'events' && (
          <Button onClick={() => setShowPublish(true)} variant="primary" className="gap-1.5">
            <Send className="w-3.5 h-3.5" />发布事件
          </Button>
        )}
        {tab === 'subscriptions' && (
          <Button onClick={() => setShowSubscribe(true)} variant="primary" className="gap-1.5">
            <Plus className="w-3.5 h-3.5" />添加订阅
          </Button>
        )}
        {tab === 'nodes' && (
          <Button onClick={() => setShowRegisterNode(true)} variant="primary" className="gap-1.5">
            <Plus className="w-3.5 h-3.5" />注册节点
          </Button>
        )}
      </div>

      {/* ── Events Tab ── */}
      {tab === 'events' && (
        <div className="space-y-1.5">
          {events.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <Zap className="w-10 h-10 mb-2 opacity-30" />
              <p className="text-xs">暂无事件</p>
              <p className="text-[10px] mt-1">事件总线收集系统中所有组件的事件</p>
            </div>
          ) : (
            events.slice().reverse().map((evt) => (
              <div
                key={evt.id}
                className="rounded-lg border border-border bg-card hover:bg-muted/20 transition-colors cursor-pointer"
                onClick={() => setExpandedEvent(expandedEvent === evt.id ? null : evt.id)}
              >
                <div className="flex items-center gap-3 p-2.5">
                  <Zap className="w-3.5 h-3.5 text-amber-500 shrink-0" />
                  <Badge variant="bare" className={`border ${topicColor(evt.topic)}`}>{evt.topic}</Badge>
                  {evt.source && <span className="text-[10px] text-muted-foreground font-mono">{evt.source}</span>}
                  <span className="text-[10px] text-muted-foreground ml-auto shrink-0">{formatRelative(evt.timestamp)}</span>
                  {expandedEvent === evt.id ? <ChevronDown className="w-3 h-3 text-muted-foreground" /> : <ChevronRight className="w-3 h-3 text-muted-foreground" />}
                </div>
                {expandedEvent === evt.id && (
                  <div className="px-2.5 pb-2.5 border-t border-border pt-2 space-y-1.5">
                    <div className="text-[10px] text-muted-foreground">
                      <span className="font-medium">ID:</span> <span className="font-mono">{evt.id}</span>
                      <span className="ml-3">时间: {formatTime(evt.timestamp)}</span>
                    </div>
                    {evt.delivered_to.length > 0 && (
                      <div className="text-[10px] text-muted-foreground">
                        投递至: {evt.delivered_to.join(', ')}
                      </div>
                    )}
                    <pre className="text-[10px] bg-muted p-2 rounded font-mono whitespace-pre-wrap break-all max-h-[150px] overflow-auto">
                      {JSON.stringify(evt.data, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {/* ── Subscriptions Tab ── */}
      {tab === 'subscriptions' && (
        <div className="space-y-2">
          {subscriptions.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <Users className="w-10 h-10 mb-2 opacity-30" />
              <p className="text-xs">暂无订阅</p>
            </div>
          ) : (
            subscriptions.map((sub) => (
              <div key={sub.id} className="rounded-lg border border-border bg-card p-3 hover:bg-muted/30 transition-colors">
                <div className="flex items-center justify-between">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <Radio className="w-3.5 h-3.5 text-purple-500 shrink-0" />
                      <span className="text-xs font-medium font-mono">{sub.id}</span>
                      <Badge variant="bare" className={`border ${topicColor(sub.topic_pattern)}`}>{sub.topic_pattern}</Badge>
                    </div>
                    <div className="flex items-center gap-3 mt-1 text-[10px] text-muted-foreground ml-5">
                      <span>投递: {sub.delivery_count} 次</span>
                      {sub.last_delivery && <span>最后: {formatRelative(sub.last_delivery)}</span>}
                      {sub.callback_url && <span className="font-mono">{sub.callback_url}</span>}
                      <span>注册: {formatTime(sub.created_at)}</span>
                    </div>
                  </div>
                  <Button onClick={() => handleUnsubscribe(sub.id)} variant="icon-md" className="hover:bg-red-500/10" title="取消订阅">
                    <Unplug className="w-3.5 h-3.5 text-red-500" />
                  </Button>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* ── Nodes Tab ── */}
      {tab === 'nodes' && (
        <div className="space-y-2">
          {nodes.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <Server className="w-10 h-10 mb-2 opacity-30" />
              <p className="text-xs">暂无注册节点</p>
            </div>
          ) : (
            nodes.map((node) => (
              <div key={node.node_id} className={`rounded-lg border p-3 hover:bg-muted/30 transition-colors ${
                node.status === 'online' ? 'border-border bg-card' : 'border-red-500/20 bg-red-500/5'
              }`}>
                <div className="flex items-center justify-between">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      {node.status === 'online' ? (
                        <Wifi className="w-3.5 h-3.5 text-green-500 shrink-0" />
                      ) : (
                        <WifiOff className="w-3.5 h-3.5 text-red-500 shrink-0" />
                      )}
                      <span className="text-xs font-medium font-mono">{node.node_id}</span>
                      <Badge variant="default">{node.node_type}</Badge>
                      <Badge variant="bare" className={`${node.status === 'online' ? 'bg-emerald-500/10 text-emerald-500' : 'bg-red-500/10 text-red-500'}`}>
                        {node.status}
                      </Badge>
                    </div>
                    <div className="flex items-center gap-3 mt-1 text-[10px] text-muted-foreground ml-5">
                      <span>事件: {node.event_count}</span>
                      <span>注册: {formatTime(node.registered_at)}</span>
                      <span>心跳: {formatRelative(node.last_heartbeat)}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <Button onClick={() => handleHeartbeat(node.node_id)} variant="icon-md" className="hover:bg-blue-500/10" title="发送心跳">
                      <Activity className="w-3.5 h-3.5 text-blue-500" />
                    </Button>
                    <Button onClick={() => handleRemoveNode(node.node_id)} variant="icon-md" className="hover:bg-red-500/10" title="移除">
                      <Trash2 className="w-3.5 h-3.5 text-red-500" />
                    </Button>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* ── Publish Dialog ── */}
      {showPublish && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowPublish(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-md mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">发布事件</h2>
              <Button onClick={() => setShowPublish(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">主题 *</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono" value={publishForm.topic} onChange={(e) => setPublishForm(f => ({ ...f, topic: e.target.value }))} placeholder="soma.heartbeat" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">来源</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={publishForm.source} onChange={(e) => setPublishForm(f => ({ ...f, source: e.target.value }))} placeholder="admin-ui" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">数据 (JSON)</label>
                <textarea className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono h-24 resize-none" value={publishForm.data} onChange={(e) => setPublishForm(f => ({ ...f, data: e.target.value }))} />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowPublish(false)} variant="secondary">取消</Button>
                <Button onClick={handlePublish} disabled={!publishForm.topic || publishing} variant="primary">
                  {publishing ? <Loader2 className="w-3 h-3 animate-spin" /> : <Send className="w-3 h-3" />}发布
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Subscribe Dialog ── */}
      {showSubscribe && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowSubscribe(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-md mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">添加订阅</h2>
              <Button onClick={() => setShowSubscribe(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">订阅者ID *</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={subscribeForm.subscriber_id} onChange={(e) => setSubscribeForm(f => ({ ...f, subscriber_id: e.target.value }))} placeholder="my-subscriber" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">主题模式 * (支持通配符)</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono" value={subscribeForm.topic_pattern} onChange={(e) => setSubscribeForm(f => ({ ...f, topic_pattern: e.target.value }))} placeholder="soma.* / organ.heartbeat / *" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">回调URL</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono" value={subscribeForm.callback_url} onChange={(e) => setSubscribeForm(f => ({ ...f, callback_url: e.target.value }))} placeholder="http://..." />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowSubscribe(false)} variant="secondary">取消</Button>
                <Button onClick={handleSubscribe} variant="primary">
                  <Plus className="w-3 h-3" />订阅
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Register Node Dialog ── */}
      {showRegisterNode && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowRegisterNode(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-md mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">注册节点</h2>
              <Button onClick={() => setShowRegisterNode(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">节点ID *</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono" value={nodeForm.node_id} onChange={(e) => setNodeForm(f => ({ ...f, node_id: e.target.value }))} placeholder="soma-node-01" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">节点类型</label>
                <select className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={nodeForm.node_type} onChange={(e) => setNodeForm(f => ({ ...f, node_type: e.target.value }))}>
                  <option value="soma">Soma</option>
                  <option value="sense">Sense</option>
                  <option value="agent">Agent</option>
                  <option value="organ">Organ</option>
                  <option value="gateway">Gateway</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">元数据 (JSON)</label>
                <textarea className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono h-16 resize-none" value={nodeForm.metadata} onChange={(e) => setNodeForm(f => ({ ...f, metadata: e.target.value }))} />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowRegisterNode(false)} variant="secondary">取消</Button>
                <Button onClick={handleRegisterNode} disabled={!nodeForm.node_id} variant="primary">
                  <Server className="w-3 h-3" />注册
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Footer ── */}
      <div className="text-[10px] text-muted-foreground text-center">
        OpenNerve · 神经系统 · 事件总线 / 消息分发 / 节点管理 / SSE实时流
      </div>
    </div>
  );
}
