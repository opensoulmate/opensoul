'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Link2, Plus, Trash2, Edit3, RefreshCw, X, CheckCircle, XCircle,
  Clock, Send, Plug, Globe, ArrowUpRight, ArrowDownLeft, Activity,
  Loader2, AlertCircle, Eye, EyeOff, Copy, Settings, Wifi, WifiOff
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge, tone } from '@/components/ui/badge';

interface Connector {
  connector_id: string;
  name: string;
  type: string;
  status: string;
  endpoint?: string;
  has_secret?: boolean;
  has_callback_token?: boolean;
  headers?: Record<string, string>;
  config?: Record<string, any>;
  description?: string;
  tags?: string[];
  trigger_count?: number;
  error_count?: number;
  last_triggered?: string;
  last_error?: string;
  created_at?: string;
}

interface LinkEvent {
  event_id: string;
  connector_id: string;
  method: string;
  source_ip: string;
  payload?: any;
  created_at: string;
}

interface LinkStats {
  status: string;
  total_connectors?: number;
  active_connectors?: number;
  total_events?: number;
  total_triggers?: number;
  total_errors?: number;
}

const CONNECTOR_TYPES = [
  { value: 'all', label: '全部类型' },
  { value: 'webhook_in', label: '入站Webhook' },
  { value: 'webhook_out', label: '出站Webhook' },
  { value: 'rest_api', label: 'REST API' },
  { value: 'oa_system', label: 'OA系统' },
  { value: 'custom', label: '自定义' },
];

const STATUS_MAP: Record<string, { label: string; color: string; icon: typeof CheckCircle }> = {
  active: { label: '活跃', color: tone('success'), icon: CheckCircle },
  inactive: { label: '停用', color: tone('muted'), icon: XCircle },
  error: { label: '错误', color: tone('destructive'), icon: AlertCircle },
};

export default function LinkPage() {
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [events, setEvents] = useState<LinkEvent[]>([]);
  const [stats, setStats] = useState<LinkStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState('all');
  const [tab, setTab] = useState<'connectors' | 'events'>('connectors');
  const [showCreate, setShowCreate] = useState(false);
  const [editConnector, setEditConnector] = useState<Connector | null>(null);
  const [detailConnector, setDetailConnector] = useState<Connector | null>(null);
  const [testResult, setTestResult] = useState<Record<string, any> | null>(null);
  const [sending, setSending] = useState<string | null>(null);
  const [sendPayload, setSendPayload] = useState('{\n  "message": "hello"\n}');
  const [showSendDialog, setShowSendDialog] = useState<string | null>(null);
  // DEC-⑲：callback_token 明文只回注册响应一次——创建成功后一次性展示，关闭后不再可见
  const [createdToken, setCreatedToken] = useState<{ name: string; token: string } | null>(null);
  const [tokenCopied, setTokenCopied] = useState(false);

  // Create form state
  const [form, setForm] = useState({
    name: '', type: 'webhook_in', endpoint: '', secret: '', description: '',
    tags: '', headers: '{}', config: '{}',
  });

  const fetchConnectors = useCallback(async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams();
      if (typeFilter !== 'all') params.set('type', typeFilter);
      const qs = params.toString() ? `?${params}` : '';
      const data = await apiFetch(`/api/link/connectors${qs}`);
      setConnectors(Array.isArray(data.connectors) ? data.connectors : []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [typeFilter]);

  const fetchEvents = useCallback(async () => {
    try {
      const data = await apiFetch('/api/link/events?limit=100');
      setEvents(Array.isArray(data.events) ? data.events : []);
    } catch { /* silent */ }
  }, []);

  const fetchStats = useCallback(async () => {
    try {
      const data = await apiFetch('/api/link/stats');
      setStats(data);
    } catch { /* silent */ }
  }, []);

  useEffect(() => { fetchConnectors(); fetchStats(); }, [fetchConnectors, fetchStats]);

  const handleCreate = async () => {
    try {
      let headers = {}, config = {};
      try { headers = JSON.parse(form.headers); } catch {}
      try { config = JSON.parse(form.config); } catch {}
      const createdName = form.name;
      const data = await apiFetch('/api/link/connectors', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: form.name, type: form.type, endpoint: form.endpoint,
          secret: form.secret || undefined, description: form.description,
          tags: form.tags ? form.tags.split(',').map(t => t.trim()) : [],
          headers, config,
        }),
      });
      setShowCreate(false);
      resetForm();
      // register 签发回调凭据明文只回此注册响应一次（DEC-⑲）——一次性展示，关闭后不再可见
      if (data?.callback_token) {
        setTokenCopied(false);
        setCreatedToken({ name: data.name || createdName, token: data.callback_token });
      }
      await fetchConnectors();
    } catch (e: any) { setError(e.message); }
  };

  const handleUpdate = async () => {
    if (!editConnector) return;
    try {
      await apiFetch(`/api/link/connectors/${editConnector.connector_id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: form.name || undefined,
          endpoint: form.endpoint || undefined,
          description: form.description || undefined,
          status: undefined,
        }),
      });
      setEditConnector(null);
      resetForm();
      await fetchConnectors();
    } catch (e: any) { setError(e.message); }
  };

  const handleDelete = async (id: string, name: string) => {
    if (!confirm(`确定要删除连接器「${name}」？`)) return;
    try {
      await apiFetch(`/api/link/connectors/${id}`, { method: 'DELETE' });
      await fetchConnectors();
    } catch (e: any) { setError(e.message); }
  };

  const handleTest = async (id: string) => {
    try {
      setSending(id);
      const data = await apiFetch(`/api/link/connectors/${id}/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ payload: { test: true, timestamp: Date.now() } }),
      });
      setTestResult(data);
    } catch (e: any) {
      setTestResult({ success: false, error: e.message });
    } finally { setSending(null); }
  };

  const handleSend = async (id: string) => {
    try {
      setSending(id);
      let payload;
      try { payload = JSON.parse(sendPayload); } catch { payload = { raw: sendPayload }; }
      await apiFetch(`/api/link/connectors/${id}/send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ payload }),
      });
      setShowSendDialog(null);
      await fetchEvents();
    } catch (e: any) { setError(e.message); }
    finally { setSending(null); }
  };

  const resetForm = () => setForm({
    name: '', type: 'webhook_in', endpoint: '', secret: '', description: '',
    tags: '', headers: '{}', config: '{}',
  });

  const openEdit = (c: Connector) => {
    setForm({
      name: c.name, type: c.type, endpoint: c.endpoint || '',
      secret: '', description: c.description || '',
      tags: (c.tags || []).join(', '),
      headers: JSON.stringify(c.headers || {}, null, 2),
      config: JSON.stringify(c.config || {}, null, 2),
    });
    setEditConnector(c);
  };

  const filtered = connectors.filter((c) => {
    if (search) {
      const q = search.toLowerCase();
      return c.name.toLowerCase().includes(q) || c.connector_id.toLowerCase().includes(q)
        || (c.description || '').toLowerCase().includes(q)
        || (c.endpoint || '').toLowerCase().includes(q);
    }
    return true;
  });

  const activeCount = connectors.filter(c => c.status === 'active').length;
  const errorCount = connectors.filter(c => c.status === 'error').length;

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{connectors.length}</div>
          <div className="text-xs text-muted-foreground">连接器总数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-emerald-600">{activeCount}</div>
          <div className="text-xs text-muted-foreground">活跃连接</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-red-500">{errorCount}</div>
          <div className="text-xs text-muted-foreground">异常连接</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-500">{events.length}</div>
          <div className="text-xs text-muted-foreground">事件记录</div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-border">
        {(['connectors', 'events'] as const).map((t) => (
          <Button variant="bare" key={t} onClick={() => { setTab(t); if (t === 'events') fetchEvents(); }}
            className={`px-4 py-2 text-xs lg:text-sm font-medium border-b-2 transition-colors ${
              tab === t ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}>
            {t === 'connectors' ? '连接器管理' : '事件日志'}
          </Button>
        ))}
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-500/10 text-red-500 text-xs">
          <AlertCircle className="w-3.5 h-3.5" />
          <span className="flex-1">{error}</span>
          <Button variant="bare" onClick={() => setError('')} className="p-0.5"><X className="w-3.5 h-3.5" /></Button>
        </div>
      )}

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder={tab === 'connectors' ? "搜索连接器名称、ID、端点..." : "搜索事件..."}
            value={search} onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        {tab === 'connectors' && (
          <select className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
            value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
            {CONNECTOR_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        )}
        <Button onClick={() => { fetchConnectors(); fetchStats(); fetchEvents(); }} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
        {tab === 'connectors' && (
          <Button onClick={() => { resetForm(); setShowCreate(true); }} variant="primary" className="gap-1.5">
            <Plus className="w-3.5 h-3.5" />新建连接器
          </Button>
        )}
      </div>

      {/* Connectors List */}
      {tab === 'connectors' && (
        <div className="space-y-2">
          {filtered.map((c) => {
            const st = STATUS_MAP[c.status] || STATUS_MAP.inactive;
            const StIcon = st.icon;
            return (
              <div key={c.connector_id}
                className="rounded-lg border border-border bg-card p-4 hover:bg-muted/30 transition-colors cursor-pointer"
                onClick={() => setDetailConnector(c)}>
                <div className="flex items-start gap-3">
                  <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                    {c.type === 'webhook_in' ? <ArrowDownLeft className="w-4 h-4 text-primary" /> :
                     c.type === 'webhook_out' ? <ArrowUpRight className="w-4 h-4 text-primary" /> :
                     <Plug className="w-4 h-4 text-primary" />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-medium truncate">{c.name}</h3>
                      <Badge variant="bare" className={`flex gap-0.5 ${st.color}`}>
                        <StIcon className="w-2.5 h-2.5" />{st.label}
                      </Badge>
                      <Badge variant="muted">
                        {CONNECTOR_TYPES.find(t => t.value === c.type)?.label || c.type}
                      </Badge>
                      {c.tags?.map((tag) => (
                        <Badge variant="info" key={tag}>{tag}</Badge>
                      ))}
                    </div>
                    {c.description && <p className="text-xs text-muted-foreground mt-1 line-clamp-1">{c.description}</p>}
                    {c.endpoint && (
                      <div className="flex items-center gap-1 mt-1 text-[10px] text-muted-foreground font-mono">
                        <Globe className="w-3 h-3" />{c.endpoint.length > 60 ? c.endpoint.slice(0, 60) + '...' : c.endpoint}
                      </div>
                    )}
                    <div className="flex items-center gap-3 mt-2 text-[10px] text-muted-foreground">
                      <span>ID: {c.connector_id.slice(0, 8)}...</span>
                      {c.trigger_count !== undefined && <span>触发 {c.trigger_count} 次</span>}
                      {c.error_count !== undefined && c.error_count > 0 && <span className="text-red-500">错误 {c.error_count}</span>}
                      {c.last_triggered && <span>最后触发: {new Date(c.last_triggered).toLocaleString('zh-CN')}</span>}
                    </div>
                  </div>
                </div>
                {/* Actions */}
                <div className="flex items-center gap-1.5 mt-3 pt-3 border-t border-border" onClick={(e) => e.stopPropagation()}>
                  <Button onClick={() => handleTest(c.connector_id)}
                    disabled={sending === c.connector_id}
                    variant="chip-xs" className="flex items-center gap-1 bg-blue-500/10 text-blue-500 hover:bg-blue-500/20 disabled:opacity-50">
                    {sending === c.connector_id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Wifi className="w-3 h-3" />}
                    测试
                  </Button>
                  <Button onClick={() => setShowSendDialog(c.connector_id)}
                    variant="chip-xs" className="flex items-center gap-1 bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20">
                    <Send className="w-3 h-3" />发送
                  </Button>
                  <Button onClick={() => openEdit(c)}
                    variant="chip-xs" className="flex items-center gap-1 bg-muted hover:bg-muted">
                    <Edit3 className="w-3 h-3" />编辑
                  </Button>
                  <Button onClick={() => handleDelete(c.connector_id, c.name)}
                    variant="chip-xs" className="flex items-center gap-1 bg-red-500/10 text-red-500 hover:bg-red-500/20">
                    <Trash2 className="w-3 h-3" />删除
                  </Button>
                </div>
              </div>
            );
          })}
          {filtered.length === 0 && (
            <div className="text-center py-12 text-muted-foreground text-sm">
              <Link2 className="w-8 h-8 mx-auto mb-2 opacity-30" />
              <p>暂无连接器</p>
              <Button variant="bare" onClick={() => { resetForm(); setShowCreate(true); }}
                className="mt-2 text-xs lg:text-sm text-primary hover:underline">创建第一个连接器</Button>
            </div>
          )}
        </div>
      )}

      {/* Events List */}
      {tab === 'events' && (
        <div className="space-y-1">
          {events.filter((e) => {
            if (!search) return true;
            const q = search.toLowerCase();
            return e.connector_id.toLowerCase().includes(q) || e.method.toLowerCase().includes(q)
              || e.source_ip.toLowerCase().includes(q) || (e.event_id || '').toLowerCase().includes(q);
          }).map((e) => (
            <div key={e.event_id} className="rounded-lg border border-border bg-card p-3 hover:bg-muted/30 transition-colors">
              <div className="flex items-center gap-2">
                <Badge variant="info" className="font-mono">{e.method}</Badge>
                <span className="text-xs font-medium">{e.connector_id.slice(0, 12)}...</span>
                <span className="text-[10px] text-muted-foreground">来自 {e.source_ip}</span>
                <span className="text-[10px] text-muted-foreground ml-auto">{new Date(e.created_at).toLocaleString('zh-CN')}</span>
              </div>
              {e.payload && (
                <pre className="mt-2 text-[10px] text-muted-foreground bg-muted rounded p-2 overflow-x-auto max-h-24">
                  {typeof e.payload === 'string' ? e.payload : JSON.stringify(e.payload, null, 2)}
                </pre>
              )}
            </div>
          ))}
          {events.length === 0 && (
            <div className="text-center py-12 text-muted-foreground text-sm">
              <Activity className="w-8 h-8 mx-auto mb-2 opacity-30" />
              <p>暂无事件记录</p>
            </div>
          )}
        </div>
      )}

      {/* Create/Edit Dialog */}
      {(showCreate || editConnector) && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => { setShowCreate(false); setEditConnector(null); }}>
          <div className="bg-card rounded-xl border border-border shadow-xl w-full max-w-lg mx-4 max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-semibold">{editConnector ? '编辑连接器' : '新建连接器'}</h2>
              <Button onClick={() => { setShowCreate(false); setEditConnector(null); }} variant="icon-sm" className="hover:bg-muted">
                <X className="w-4 h-4" />
              </Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">名称 *</label>
                <input className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                  value={form.name} onChange={(e) => setForm(f => ({ ...f, name: e.target.value }))} placeholder="连接器名称" />
              </div>
              {!editConnector && (
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">类型 *</label>
                  <select className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
                    value={form.type} onChange={(e) => setForm(f => ({ ...f, type: e.target.value }))}>
                    {CONNECTOR_TYPES.filter(t => t.value !== 'all').map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                  </select>
                </div>
              )}
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">端点 URL</label>
                <input className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                  value={form.endpoint} onChange={(e) => setForm(f => ({ ...f, endpoint: e.target.value }))} placeholder="https://example.com/webhook" />
              </div>
              {!editConnector && (
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">密钥 (可选)</label>
                  <input className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                    type="password" value={form.secret} onChange={(e) => setForm(f => ({ ...f, secret: e.target.value }))} placeholder="Webhook签名密钥" />
                </div>
              )}
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">描述</label>
                <textarea className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 resize-none"
                  rows={2} value={form.description} onChange={(e) => setForm(f => ({ ...f, description: e.target.value }))} placeholder="连接器用途描述" />
              </div>
              {!editConnector && (
                <>
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">标签 (逗号分隔)</label>
                    <input className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                      value={form.tags} onChange={(e) => setForm(f => ({ ...f, tags: e.target.value }))} placeholder="production, api, monitoring" />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">自定义 Headers (JSON)</label>
                    <textarea className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 font-mono resize-none"
                      rows={2} value={form.headers} onChange={(e) => setForm(f => ({ ...f, headers: e.target.value }))} />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">配置 (JSON)</label>
                    <textarea className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 font-mono resize-none"
                      rows={2} value={form.config} onChange={(e) => setForm(f => ({ ...f, config: e.target.value }))} />
                  </div>
                </>
              )}
            </div>
            <div className="flex items-center justify-end gap-2 p-4 border-t border-border">
              <Button onClick={() => { setShowCreate(false); setEditConnector(null); }} variant="secondary">取消</Button>
              <Button onClick={editConnector ? handleUpdate : handleCreate}
                disabled={!form.name} variant="primary">
                {editConnector ? '保存' : '创建'}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Send Dialog */}
      {showSendDialog && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowSendDialog(null)}>
          <div className="bg-card rounded-xl border border-border shadow-xl w-full max-w-md mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-semibold">发送 Webhook</h2>
              <Button onClick={() => setShowSendDialog(null)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4">
              <label className="text-xs text-muted-foreground mb-1 block">Payload (JSON)</label>
              <textarea className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 font-mono resize-none"
                rows={6} value={sendPayload} onChange={(e) => setSendPayload(e.target.value)} />
            </div>
            <div className="flex items-center justify-end gap-2 p-4 border-t border-border">
              <Button onClick={() => setShowSendDialog(null)} variant="secondary">取消</Button>
              <Button onClick={() => handleSend(showSendDialog)} disabled={sending === showSendDialog} variant="primary" className="gap-1.5">
                {sending === showSendDialog ? <Loader2 className="w-3 h-3 animate-spin" /> : <Send className="w-3 h-3" />}
                发送
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Detail Dialog */}
      {detailConnector && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setDetailConnector(null)}>
          <div className="bg-card rounded-xl border border-border shadow-xl w-full max-w-lg mx-4 max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-semibold">连接器详情</h2>
              <Button onClick={() => setDetailConnector(null)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3 text-xs">
              <div className="grid grid-cols-2 gap-3">
                <div><span className="text-muted-foreground">ID:</span> <span className="font-mono">{detailConnector.connector_id}</span></div>
                <div><span className="text-muted-foreground">名称:</span> {detailConnector.name}</div>
                <div><span className="text-muted-foreground">类型:</span> {CONNECTOR_TYPES.find(t => t.value === detailConnector.type)?.label || detailConnector.type}</div>
                <div>
                  <span className="text-muted-foreground">状态:</span>{' '}
                  <span className={STATUS_MAP[detailConnector.status]?.color || ''}>
                    {STATUS_MAP[detailConnector.status]?.label || detailConnector.status}
                  </span>
                </div>
                {detailConnector.endpoint && <div className="col-span-2"><span className="text-muted-foreground">端点:</span> <span className="font-mono break-all">{detailConnector.endpoint}</span></div>}
                {detailConnector.description && <div className="col-span-2"><span className="text-muted-foreground">描述:</span> {detailConnector.description}</div>}
                <div><span className="text-muted-foreground">有密钥:</span> {detailConnector.has_secret ? '是' : '否'}</div>
                <div><span className="text-muted-foreground">回调凭据:</span> {detailConnector.has_callback_token ? '已签发' : '未签发'}</div>
                <div><span className="text-muted-foreground">触发次数:</span> {detailConnector.trigger_count ?? 0}</div>
                <div><span className="text-muted-foreground">错误次数:</span> <span className={detailConnector.error_count ? 'text-red-500' : ''}>{detailConnector.error_count ?? 0}</span></div>
                {detailConnector.last_triggered && <div><span className="text-muted-foreground">最后触发:</span> {new Date(detailConnector.last_triggered).toLocaleString('zh-CN')}</div>}
                {detailConnector.last_error && <div className="col-span-2"><span className="text-muted-foreground">最后错误:</span> <span className="text-red-500">{detailConnector.last_error}</span></div>}
                {detailConnector.created_at && <div><span className="text-muted-foreground">创建时间:</span> {new Date(detailConnector.created_at).toLocaleString('zh-CN')}</div>}
              </div>
              {detailConnector.tags && detailConnector.tags.length > 0 && (
                <div>
                  <span className="text-muted-foreground">标签: </span>
                  {detailConnector.tags.map((tag) => (
                    <Badge variant="info" key={tag} className="mr-1">{tag}</Badge>
                  ))}
                </div>
              )}
              {detailConnector.headers && Object.keys(detailConnector.headers).length > 0 && (
                <div>
                  <span className="text-muted-foreground">Headers:</span>
                  <pre className="mt-1 bg-muted rounded p-2 overflow-x-auto font-mono">{JSON.stringify(detailConnector.headers, null, 2)}</pre>
                </div>
              )}
              {detailConnector.config && Object.keys(detailConnector.config).length > 0 && (
                <div>
                  <span className="text-muted-foreground">配置:</span>
                  <pre className="mt-1 bg-muted rounded p-2 overflow-x-auto font-mono">{JSON.stringify(detailConnector.config, null, 2)}</pre>
                </div>
              )}
            </div>
            <div className="flex items-center justify-end gap-2 p-4 border-t border-border">
              <Button onClick={() => { setDetailConnector(null); openEdit(detailConnector); }} variant="secondary" className="gap-1">
                <Edit3 className="w-3 h-3" />编辑
              </Button>
              <Button onClick={() => setDetailConnector(null)} variant="primary">关闭</Button>
            </div>
          </div>
        </div>
      )}


      {/* DEC-⑲ 回调凭据一次性展示：明文只回注册响应一次，关闭后不再可见 */}
      {createdToken && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setCreatedToken(null)}>
          <div className="bg-card rounded-xl border border-border shadow-xl w-full max-w-md mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-semibold">回调凭据已签发</h2>
              <Button onClick={() => setCreatedToken(null)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3 text-xs">
              <div className="flex items-start gap-2 p-2 rounded-lg bg-amber-500/10 text-amber-600">
                <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                <span>明文仅此一次展示，关闭后不再可见（接口只回签发状态）。请立即复制并配置到发方。</span>
              </div>
              <div><span className="text-muted-foreground">连接器:</span> {createdToken.name}</div>
              <div>
                <span className="text-muted-foreground mb-1 block">X-Callback-Token</span>
                <div className="flex items-start gap-1.5">
                  <pre className="flex-1 bg-muted rounded p-2 font-mono break-all select-all">{createdToken.token}</pre>
                  <Button variant="chip-xs" className="flex items-center gap-1 bg-primary/10 text-primary hover:bg-primary/20"
                    onClick={() => {
                      if (typeof navigator !== 'undefined' && navigator.clipboard) navigator.clipboard.writeText(createdToken.token);
                      setTokenCopied(true);
                    }}>
                    <Copy className="w-3 h-3" />{tokenCopied ? '已复制' : '复制'}
                  </Button>
                </div>
              </div>
              <p className="text-[10px] text-muted-foreground">
                发方调用 POST /api/link/webhook/{'{connector_id}'} 时须带 X-Callback-Token 头（或 X-Signature HMAC 等效证明），缺证 401。
              </p>
            </div>
            <div className="flex items-center justify-end gap-2 p-4 border-t border-border">
              <Button onClick={() => setCreatedToken(null)} variant="primary">我已保存</Button>
            </div>
          </div>
        </div>
      )}
      {/* Test Result Toast */}
      {testResult && (
        <div className="fixed bottom-4 right-4 z-50 max-w-sm">
          <div className={`rounded-lg border shadow-lg p-3 text-xs ${testResult.success === false ? 'bg-red-500/10 border-red-500/20' : 'bg-emerald-500/10 border-emerald-500/20'}`}>
            <div className="flex items-center gap-2 mb-1">
              {testResult.success === false ? <XCircle className="w-4 h-4 text-red-500" /> : <CheckCircle className="w-4 h-4 text-emerald-600" />}
              <span className="font-medium">{testResult.success === false ? '测试失败' : '测试成功'}</span>
            </div>
            <pre className="text-[10px] text-muted-foreground max-h-24 overflow-auto">{JSON.stringify(testResult, null, 2)}</pre>
            <Button variant="bare" onClick={() => setTestResult(null)} className="mt-2 text-[10px] text-primary hover:underline">关闭</Button>
          </div>
        </div>
      )}
    </div>
  );
}
