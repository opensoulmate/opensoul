'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, RefreshCw, X, Volume2, Send, Radio, Plus, Trash2,
  Edit3, Save, CheckCircle, XCircle, AlertTriangle, Clock,
  ChevronDown, ChevronRight, Settings, Eye, Zap, Globe,
  MessageSquare, FileText, Activity, Loader2, Wifi, WifiOff,
  Copy, Play, ToggleLeft, ToggleRight,
} from 'lucide-react';
import { Button } from '@/components/ui/button';

// ── Types ───────────────────────────────────────────────────

interface ChannelInfo {
  channel: string;
  enabled: boolean;
  has_endpoint: boolean;
  has_token: boolean;
}

interface ChannelHealth {
  channel: string;
  status: string;
  latency_ms: number;
  error: string;
}

interface Template {
  template_id: string;
  name: string;
  description: string;
  channel: string;
  title_template: string;
  content_template: string;
  variables: string[];
  category: string;
  icon: string;
  usage_count: number;
  last_used: string | null;
  created_at: string;
}

interface HistoryMessage {
  channel: string;
  title: string;
  content: string;
  success: boolean;
  msg_id: string;
  error: string;
  timestamp: string;
}

interface EchoStats {
  total_sent: number;
  total_failed: number;
  by_channel: Record<string, number>;
  templates: { total: number; categories: string[] };
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

const CHANNEL_LABELS: Record<string, string> = {
  console: '控制台',
  webhook: 'Webhook',
  telegram: 'Telegram',
  email: '邮件',
  dingtalk: '钉钉',
  feishu: '飞书',
  wechat_work: '企业微信',
  slack: 'Slack',
  discord: 'Discord',
};

const CHANNEL_ICONS: Record<string, string> = {
  console: '💻',
  webhook: '🔗',
  telegram: '✈️',
  email: '📧',
  dingtalk: '🔔',
  feishu: '🐦',
  wechat_work: '💬',
  slack: '📱',
  discord: '🎮',
};

function StatusDot({ status }: { status: string }) {
  const color = status === 'ok' ? 'bg-green-500' :
    status === 'error' ? 'bg-red-500' :
    status === 'unconfigured' ? 'bg-amber-500' :
    'bg-muted-foreground/30';
  return <span className={`inline-block w-2 h-2 rounded-full ${color}`} />;
}

// ── Channel Card ────────────────────────────────────────────

function ChannelCard({ channel, health, onConfigure, onToggle }: {
  channel: ChannelInfo;
  health?: ChannelHealth;
  onConfigure: () => void;
  onToggle: () => void;
}) {
  const label = CHANNEL_LABELS[channel.channel] || channel.channel;
  const icon = CHANNEL_ICONS[channel.channel] || '📨';
  const isHealthy = health?.status === 'ok';
  const isError = health?.status === 'error';

  return (
    <div className={`rounded-lg border p-4 transition-colors ${
      isError ? 'border-red-500/30 bg-red-500/5' :
      isHealthy ? 'border-green-500/20 bg-card' :
      'border-border bg-card'
    }`}>
      <div className="flex items-center gap-3 mb-3">
        <span className="text-2xl">{icon}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">{label}</span>
            <StatusDot status={health?.status || (channel.enabled ? 'unknown' : 'disabled')} />
          </div>
          <div className="text-[10px] text-muted-foreground font-mono">{channel.channel}</div>
        </div>
        <button
          onClick={onToggle}
          className={`p-1.5 rounded-md transition-colors ${
            channel.enabled ? 'text-green-500 hover:bg-emerald-500/10' : 'text-muted-foreground hover:bg-muted'
          }`}
          title={channel.enabled ? '禁用' : '启用'}
        >
          {channel.enabled ? <ToggleRight className="w-5 h-5" /> : <ToggleLeft className="w-5 h-5" />}
        </button>
      </div>

      <div className="flex items-center gap-2 text-[10px] text-muted-foreground mb-3">
        <span className={`px-1.5 py-0.5 rounded ${channel.has_endpoint ? 'bg-emerald-500/10 text-emerald-500' : 'bg-muted text-muted-foreground'}`}>
          {channel.has_endpoint ? '✓ 端点' : '✗ 无端点'}
        </span>
        <span className={`px-1.5 py-0.5 rounded ${channel.has_token ? 'bg-emerald-500/10 text-emerald-500' : 'bg-muted text-muted-foreground'}`}>
          {channel.has_token ? '✓ Token' : '✗ 无Token'}
        </span>
        {health && (
          <span className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
            {health.latency_ms}ms
          </span>
        )}
      </div>

      {health?.error && (
        <div className="text-[10px] text-red-400 mb-2 truncate">{health.error}</div>
      )}

      <Button onClick={onConfigure} variant="secondary" className="w-full">
        <Settings className="w-3 h-3" />配置
      </Button>
    </div>
  );
}

// ── Template Card ───────────────────────────────────────────

function TemplateCard({ template, expanded, onToggle, onSend, onEdit, onDelete }: {
  template: Template;
  expanded: boolean;
  onToggle: () => void;
  onSend: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="border border-border rounded-lg bg-card">
      <button
        onClick={onToggle}
        className="flex items-center gap-3 w-full p-3 text-left hover:bg-muted/30 transition-colors"
      >
        <div className="w-10 h-10 rounded-lg bg-purple-500/10 flex items-center justify-center shrink-0">
          <span className="text-lg">{template.icon || '📨'}</span>
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">{template.name}</span>
            <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-muted text-muted-foreground">
              {template.category}
            </span>
            <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-primary/10 text-primary">
              {CHANNEL_LABELS[template.channel] || template.channel}
            </span>
          </div>
          <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">{template.description || template.title_template}</p>
          <div className="flex items-center gap-3 mt-0.5 text-[10px] text-muted-foreground">
            <span>使用: {template.usage_count}次</span>
            {template.variables.length > 0 && <span>变量: {template.variables.length}</span>}
          </div>
        </div>
        <div className="text-right shrink-0 mr-2">
          <div className="text-[10px] text-muted-foreground">{formatTime(template.created_at)}</div>
        </div>
        {expanded ? <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 text-muted-foreground shrink-0" />}
      </button>

      {expanded && (
        <div className="px-3 pb-3 border-t border-border pt-2 space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="rounded-lg border border-border bg-muted/30 p-3">
              <div className="text-[10px] text-muted-foreground mb-1">标题模板</div>
              <p className="text-xs font-mono">{template.title_template}</p>
            </div>
            <div className="rounded-lg border border-border bg-muted/30 p-3">
              <div className="text-[10px] text-muted-foreground mb-1">内容模板</div>
              <p className="text-xs font-mono line-clamp-4">{template.content_template}</p>
            </div>
          </div>

          {template.variables.length > 0 && (
            <div className="flex flex-wrap gap-1">
              <span className="text-[10px] text-muted-foreground mr-1">变量:</span>
              {template.variables.map((v) => (
                <span key={v} className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-amber-500/10 text-amber-500 font-mono">
                  {`{{${v}}}`}
                </span>
              ))}
            </div>
          )}

          <div className="flex items-center gap-2">
            <Button onClick={onSend} variant="primary" className="gap-1.5">
              <Send className="w-3 h-3" />发送
            </Button>
            <Button onClick={onEdit} variant="secondary">
              <Edit3 className="w-3 h-3" />编辑
            </Button>
            <button
              onClick={onDelete}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm bg-red-500/10 text-red-500 border border-red-500/30 rounded-lg hover:bg-red-500/20"
            >
              <Trash2 className="w-3 h-3" />删除
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Configure Channel Dialog ────────────────────────────────

function ConfigureDialog({ channel, onClose, onSave }: {
  channel: string;
  onClose: () => void;
  onSave: (endpoint: string, token: string, enabled: boolean) => void;
}) {
  const [endpoint, setEndpoint] = useState('');
  const [token, setToken] = useState('');
  const [enabled, setEnabled] = useState(true);
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      await apiFetch('/api/echo/channels/configure', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel, endpoint, token, enabled }),
      });
      onSave(endpoint, token, enabled);
    } catch (e) {
      // ignore
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div className="w-full max-w-md rounded-xl border border-border bg-card p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 mb-4">
          <Settings className="w-4 h-4" />
          <span className="text-sm font-medium">配置 {CHANNEL_LABELS[channel] || channel}</span>
          <button onClick={onClose} className="ml-auto p-1 hover:bg-muted rounded-md"><X className="w-4 h-4" /></button>
        </div>

        <div className="space-y-3">
          <div>
            <label className="text-xs text-muted-foreground mb-1 block">端点 URL</label>
            <input
              className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
              placeholder="https://..."
              value={endpoint}
              onChange={(e) => setEndpoint(e.target.value)}
            />
          </div>
          <div>
            <label className="text-xs text-muted-foreground mb-1 block">Token / API Key</label>
            <input
              type="password"
              className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
              placeholder="可选"
              value={token}
              onChange={(e) => setToken(e.target.value)}
            />
          </div>
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} className="rounded" />
            <span className="text-xs">启用通道</span>
          </label>
        </div>

        <div className="flex justify-end gap-2 mt-4">
          <Button onClick={onClose} variant="secondary">取消</Button>
          <Button onClick={handleSave}
            disabled={saving} variant="primary" className="gap-1.5">
            {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
            保存
          </Button>
        </div>
      </div>
    </div>
  );
}

// ── Send Message Dialog ─────────────────────────────────────

function SendDialog({ defaultChannel, onClose }: {
  defaultChannel?: string;
  onClose: () => void;
}) {
  const [channel, setChannel] = useState(defaultChannel || 'console');
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [target, setTarget] = useState('');
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<{ success: boolean; msg_id?: string; error?: string } | null>(null);

  const handleSend = async () => {
    if (!title.trim() || !content.trim()) return;
    setSending(true);
    try {
      const data = await apiFetch('/api/echo/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel, title, content, target }),
      });
      setResult(data);
    } catch (e: any) {
      setResult({ success: false, error: e.message });
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div className="w-full max-w-md rounded-xl border border-border bg-card p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 mb-4">
          <Send className="w-4 h-4" />
          <span className="text-sm font-medium">发送消息</span>
          <button onClick={onClose} className="ml-auto p-1 hover:bg-muted rounded-md"><X className="w-4 h-4" /></button>
        </div>

        <div className="space-y-3">
          <div>
            <label className="text-xs text-muted-foreground mb-1 block">通道</label>
            <select
              className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
            >
              {Object.entries(CHANNEL_LABELS).map(([k, v]) => (
                <option key={k} value={k}>{CHANNEL_ICONS[k]} {v}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-muted-foreground mb-1 block">标题</label>
            <input
              className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
              placeholder="消息标题"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>
          <div>
            <label className="text-xs text-muted-foreground mb-1 block">内容</label>
            <textarea
              className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 min-h-[80px] resize-y"
              placeholder="消息内容"
              value={content}
              onChange={(e) => setContent(e.target.value)}
            />
          </div>
          <div>
            <label className="text-xs text-muted-foreground mb-1 block">目标（可选）</label>
            <input
              className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
              placeholder="接收者ID/地址"
              value={target}
              onChange={(e) => setTarget(e.target.value)}
            />
          </div>
        </div>

        {result && (
          <div className={`mt-3 p-2 rounded text-xs ${result.success ? 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/30' : 'bg-red-500/10 text-red-500 border border-red-500/30'}`}>
            {result.success ? `发送成功 (${result.msg_id})` : `发送失败: ${result.error}`}
          </div>
        )}

        <div className="flex justify-end gap-2 mt-4">
          <Button onClick={onClose} variant="secondary">关闭</Button>
          <Button onClick={handleSend}
            disabled={sending || !title.trim() || !content.trim()} variant="primary" className="gap-1.5">
            {sending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Send className="w-3 h-3" />}
            发送
          </Button>
        </div>
      </div>
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────

export default function EchoPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<'channels' | 'templates' | 'history'>('channels');

  // Channels
  const [channels, setChannels] = useState<ChannelInfo[]>([]);
  const [channelHealth, setChannelHealth] = useState<ChannelHealth[]>([]);
  const [configuringChannel, setConfiguringChannel] = useState<string | null>(null);

  // Templates
  const [templates, setTemplates] = useState<Template[]>([]);
  const [expandedTemplate, setExpandedTemplate] = useState<string | null>(null);
  const [templateSearch, setTemplateSearch] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('all');

  // History
  const [history, setHistory] = useState<HistoryMessage[]>([]);
  const [historyChannel, setHistoryChannel] = useState('all');

  // Stats
  const [stats, setStats] = useState<EchoStats | null>(null);

  // Dialogs
  const [showSend, setShowSend] = useState(false);
  const [sendChannel, setSendChannel] = useState<string | undefined>();

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError('');
    const results = await Promise.allSettled([
      apiFetch('/api/echo/channels'),
      apiFetch('/api/echo/channels/health'),
      apiFetch('/api/echo/templates'),
      apiFetch('/api/echo/history?limit=50'),
      apiFetch('/api/echo/stats'),
    ]);

    const [chRes, healthRes, tplRes, histRes, statsRes] = results;
    if (chRes.status === 'fulfilled') setChannels(chRes.value.channels || []);
    if (healthRes.status === 'fulfilled') setChannelHealth(healthRes.value.channels || []);
    if (tplRes.status === 'fulfilled') setTemplates(tplRes.value.templates || []);
    if (histRes.status === 'fulfilled') setHistory(histRes.value.messages || []);
    if (statsRes.status === 'fulfilled') setStats(statsRes.value);

    setLoading(false);
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  // Filter templates
  const filteredTemplates = templates.filter((t) => {
    if (categoryFilter !== 'all' && t.category !== categoryFilter) return false;
    if (templateSearch) {
      const q = templateSearch.toLowerCase();
      return t.name.toLowerCase().includes(q) ||
        t.description.toLowerCase().includes(q) ||
        t.title_template.toLowerCase().includes(q);
    }
    return true;
  });

  // Filter history
  const filteredHistory = historyChannel === 'all'
    ? history
    : history.filter((m) => m.channel === historyChannel);

  // Unique categories
  const categories = [...new Set(templates.map((t) => t.category))];

  const handleDeleteTemplate = async (templateId: string) => {
    try {
      await apiFetch(`/api/echo/templates/${templateId}`, { method: 'DELETE' });
      setTemplates((prev) => prev.filter((t) => t.template_id !== templateId));
    } catch (e: any) {
      setError(e.message);
    }
  };

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* ── Stats ── */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{channels.length}</div>
          <div className="text-xs text-muted-foreground">通道数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-500">{channelHealth.filter((h) => h.status === 'ok').length}</div>
          <div className="text-xs text-muted-foreground">健康通道</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-purple-500">{templates.length}</div>
          <div className="text-xs text-muted-foreground">消息模板</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-primary">{stats?.total_sent || 0}</div>
          <div className="text-xs text-muted-foreground">已发送</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-red-500">{stats?.total_failed || 0}</div>
          <div className="text-xs text-muted-foreground">失败</div>
        </div>
      </div>

      {/* ── Tabs ── */}
      <div className="flex items-center gap-1 border-b border-border pb-1">
        {([
          { key: 'channels', label: '通道管理', icon: <Radio className="w-3.5 h-3.5" /> },
          { key: 'templates', label: '消息模板', icon: <FileText className="w-3.5 h-3.5" /> },
          { key: 'history', label: '发送历史', icon: <Clock className="w-3.5 h-3.5" /> },
        ] as const).map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex items-center gap-1.5 px-3 py-2 text-xs lg:text-sm font-medium rounded-t-md transition-colors ${
              tab === t.key ? 'bg-primary/10 text-primary border-b-2 border-primary' : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
            }`}
          >
            {t.icon}{t.label}
          </button>
        ))}
        <div className="ml-auto flex items-center gap-2">
          <Button onClick={() => { setSendChannel(undefined); setShowSend(true); }} variant="primary" className="gap-1.5">
            <Send className="w-3 h-3" />发送消息
          </Button>
          <Button onClick={() => fetchAll()} variant="secondary">
            <RefreshCw className="w-3.5 h-3.5" />刷新
          </Button>
        </div>
      </div>

      {/* ── Channels Tab ── */}
      {tab === 'channels' && (
        <div className="space-y-4">
          <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 flex items-center gap-2">
            <Volume2 className="w-4 h-4 text-amber-500 shrink-0" />
            <div>
              <span className="text-xs font-medium text-amber-500">OpenEcho 消息通道</span>
              <p className="text-[10px] text-muted-foreground mt-0.5">
                配置多渠道消息推送，支持Webhook、Telegram、邮件、钉钉、飞书等。健康检查自动监控通道可用性。
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {channels.map((ch) => {
              const health = channelHealth.find((h) => h.channel === ch.channel);
              return (
                <ChannelCard
                  key={ch.channel}
                  channel={ch}
                  health={health}
                  onConfigure={() => setConfiguringChannel(ch.channel)}
                  onToggle={async () => {
                    try {
                      await apiFetch('/api/echo/channels/configure', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ channel: ch.channel, enabled: !ch.enabled }),
                      });
                      fetchAll();
                    } catch {}
                  }}
                />
              );
            })}
          </div>

          {channels.length === 0 && (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <Radio className="w-10 h-10 mb-2 opacity-30" />
              <p className="text-xs">暂无配置的通道</p>
            </div>
          )}
        </div>
      )}

      {/* ── Templates Tab ── */}
      {tab === 'templates' && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative flex-1 min-w-[200px]">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
              <input
                className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                placeholder="搜索模板名称..."
                value={templateSearch}
                onChange={(e) => setTemplateSearch(e.target.value)}
              />
            </div>
            <select
              className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
              value={categoryFilter}
              onChange={(e) => setCategoryFilter(e.target.value)}
            >
              <option value="all">全部分类</option>
              {categories.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>

          <div className="space-y-2">
            {filteredTemplates.map((tpl) => (
              <TemplateCard
                key={tpl.template_id}
                template={tpl}
                expanded={expandedTemplate === tpl.template_id}
                onToggle={() => setExpandedTemplate(expandedTemplate === tpl.template_id ? null : tpl.template_id)}
                onSend={() => { setSendChannel(tpl.channel); setShowSend(true); }}
                onEdit={() => {}}
                onDelete={() => handleDeleteTemplate(tpl.template_id)}
              />
            ))}
          </div>

          {filteredTemplates.length === 0 && (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <FileText className="w-10 h-10 mb-2 opacity-30" />
              <p className="text-xs">暂无消息模板</p>
            </div>
          )}
        </div>
      )}

      {/* ── History Tab ── */}
      {tab === 'history' && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <select
              className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
              value={historyChannel}
              onChange={(e) => setHistoryChannel(e.target.value)}
            >
              <option value="all">全部通道</option>
              {Object.entries(CHANNEL_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>

          <div className="space-y-1">
            {filteredHistory.map((msg, i) => (
              <div key={msg.msg_id || i} className="flex items-center gap-3 p-2.5 rounded-lg border border-border bg-card hover:bg-muted/20 transition-colors">
                <span className="text-lg shrink-0">{CHANNEL_ICONS[msg.channel] || '📨'}</span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium truncate">{msg.title}</span>
                    {msg.success ? (
                      <CheckCircle className="w-3 h-3 text-green-500 shrink-0" />
                    ) : (
                      <XCircle className="w-3 h-3 text-red-500 shrink-0" />
                    )}
                  </div>
                  <p className="text-[10px] text-muted-foreground line-clamp-1">{msg.content}</p>
                </div>
                <div className="text-right shrink-0">
                  <div className="text-[10px] text-muted-foreground">{CHANNEL_LABELS[msg.channel] || msg.channel}</div>
                  <div className="text-[10px] text-muted-foreground">{formatTime(msg.timestamp)}</div>
                </div>
              </div>
            ))}
          </div>

          {filteredHistory.length === 0 && (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <Clock className="w-10 h-10 mb-2 opacity-30" />
              <p className="text-xs">暂无发送历史</p>
            </div>
          )}
        </div>
      )}

      {/* ── Dialogs ── */}
      {configuringChannel && (
        <ConfigureDialog
          channel={configuringChannel}
          onClose={() => setConfiguringChannel(null)}
          onSave={() => { setConfiguringChannel(null); fetchAll(); }}
        />
      )}
      {showSend && (
        <SendDialog
          defaultChannel={sendChannel}
          onClose={() => { setShowSend(false); setSendChannel(undefined); }}
        />
      )}

      {/* ── Error ── */}
      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-red-500 shrink-0" />
          <span className="text-xs text-red-500">{error}</span>
          <button onClick={() => setError('')} className="ml-auto p-1 hover:bg-muted rounded-md">
            <X className="w-3 h-3" />
          </button>
        </div>
      )}
    </div>
  );
}
