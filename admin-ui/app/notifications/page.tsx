'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Bell, Trash2, RefreshCw, X, CheckCheck, AlertCircle,
  Info, AlertTriangle, CheckCircle, Filter, Beaker, Send, Settings,
  ChevronDown, ChevronRight, BarChart3, Loader2, Zap, Globe,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

interface Notification {
  id: string;
  source: string;
  title: string;
  body: string;
  level: string;
  organ: string;
  emoji: string;
  action_url: string;
  metadata: Record<string, any>;
  timestamp: number;
  read: boolean;
}

interface ForwardRules {
  enabled: boolean;
  rules: Record<string, string[]>;
  min_priority: number;
}

interface NotifStats {
  total: number;
  unread: number;
  by_level: Record<string, number>;
  by_organ: Record<string, number>;
}

const LEVEL_CONFIG: Record<string, { icon: any; color: string; bg: string; label: string }> = {
  info: { icon: Info, color: 'text-blue-500', bg: 'bg-blue-500/10', label: '信息' },
  warning: { icon: AlertTriangle, color: 'text-orange-500', bg: 'bg-orange-500/10', label: '警告' },
  error: { icon: AlertCircle, color: 'text-red-500', bg: 'bg-red-500/10', label: '错误' },
  success: { icon: CheckCircle, color: 'text-green-500', bg: 'bg-green-500/10', label: '成功' },
};

export default function NotificationsPage() {
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [levelFilter, setLevelFilter] = useState('');
  const [sourceFilter, setSourceFilter] = useState('');
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [unreadCount, setUnreadCount] = useState(0);

  // Stats
  const [stats, setStats] = useState<NotifStats | null>(null);
  const [showStats, setShowStats] = useState(false);

  // Forward rules
  const [forwardRules, setForwardRules] = useState<ForwardRules | null>(null);
  const [showForwardRules, setShowForwardRules] = useState(false);
  const [forwardLoading, setForwardLoading] = useState(false);

  // Detail
  const [selectedNotif, setSelectedNotif] = useState<Notification | null>(null);

  // Forward dialog
  const [forwardNotif, setForwardNotif] = useState<Notification | null>(null);
  const [forwardChannel, setForwardChannel] = useState('');
  const [forwarding, setForwarding] = useState(false);

  const fetchNotifications = useCallback(async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams({ limit: '100' });
      if (unreadOnly) params.set('unread_only', 'true');
      if (levelFilter) params.set('level', levelFilter);
      if (sourceFilter) params.set('source', sourceFilter);
      const data = await apiFetch(`/api/notifications/recent?${params}`);
      setNotifications(Array.isArray(data.notifications) ? data.notifications : []);
      setUnreadCount(data.unread_count || 0);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [unreadOnly, levelFilter, sourceFilter]);

  const fetchStats = useCallback(async () => {
    try {
      const data = await apiFetch('/api/notifications/stats');
      setStats(data);
    } catch {}
  }, []);

  const fetchForwardRules = useCallback(async () => {
    try {
      const data = await apiFetch('/api/notifications/forward/rules');
      setForwardRules(data);
    } catch {}
  }, []);

  useEffect(() => { fetchNotifications(); fetchStats(); fetchForwardRules(); }, [fetchNotifications, fetchStats, fetchForwardRules]);

  // Auto-refresh unread count
  useEffect(() => {
    const iv = setInterval(async () => {
      try {
        const data = await apiFetch('/api/notifications/unread-count');
        setUnreadCount(data.unread_count || 0);
      } catch {}
    }, 30000);
    return () => clearInterval(iv);
  }, []);

  const handleMarkRead = async (notifId: string) => {
    try {
      await apiFetch(`/api/notifications/${notifId}/read`, { method: 'POST' });
      setNotifications((prev) => prev.map((n) => n.id === notifId ? { ...n, read: true } : n));
      setUnreadCount((c) => Math.max(0, c - 1));
    } catch (e: any) { setError(e.message); }
  };

  const handleMarkAllRead = async () => {
    try {
      await apiFetch('/api/notifications/read-all', { method: 'POST' });
      setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
      setUnreadCount(0);
    } catch (e: any) { setError(e.message); }
  };

  const handleDismiss = async (notifId: string) => {
    try {
      await apiFetch(`/api/notifications/${notifId}`, { method: 'DELETE' });
      setNotifications((prev) => prev.filter((n) => n.id !== notifId));
      fetchStats();
    } catch (e: any) { setError(e.message); }
  };

  const handleClearAll = async () => {
    if (!confirm('确定要清空所有通知？')) return;
    try {
      await apiFetch('/api/notifications/', { method: 'DELETE' });
      setNotifications([]);
      setUnreadCount(0);
      fetchStats();
    } catch (e: any) { setError(e.message); }
  };

  const handleTest = async () => {
    try {
      await apiFetch('/api/notifications/test', { method: 'POST' });
      fetchNotifications();
      fetchStats();
    } catch (e: any) { setError(e.message); }
  };

  const handleTestForward = async () => {
    try {
      await apiFetch('/api/notifications/forward/test', { method: 'POST' });
      fetchNotifications();
    } catch (e: any) { setError(e.message); }
  };

  const handleToggleForward = async (enabled: boolean) => {
    try {
      await apiFetch(`/api/notifications/forward/enabled?enabled=${enabled}`, { method: 'PUT' });
      fetchForwardRules();
    } catch (e: any) { setError(e.message); }
  };

  const handleForward = async () => {
    if (!forwardNotif || !forwardChannel) return;
    setForwarding(true);
    try {
      const result = await apiFetch(`/api/notifications/${forwardNotif.id}/forward`, {
        method: 'POST',
        body: JSON.stringify({ channel: forwardChannel }),
      });
      if (result.success) {
        setForwardNotif(null);
        setForwardChannel('');
      } else {
        setError(result.error || '转发失败');
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setForwarding(false);
    }
  };

  const sources = Array.from(new Set(notifications.map((n) => n.source))).sort();

  const filtered = notifications.filter((n) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return n.title.toLowerCase().includes(q) || n.body.toLowerCase().includes(q) || n.source.toLowerCase().includes(q);
  });

  const formatTime = (ts: number) => {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  const formatRelative = (ts: number) => {
    if (!ts) return '';
    const diff = Date.now() / 1000 - ts;
    if (diff < 60) return '刚刚';
    if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`;
    return `${Math.floor(diff / 86400)}天前`;
  };

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <Bell className="w-4 h-4 text-primary" />
            <span className="text-xs text-muted-foreground">总通知</span>
          </div>
          <div className="text-2xl font-bold mt-1">{stats?.total || notifications.length}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <Zap className="w-4 h-4 text-orange-500" />
            <span className="text-xs text-muted-foreground">未读</span>
          </div>
          <div className="text-2xl font-bold text-orange-500 mt-1">{unreadCount}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-red-500" />
            <span className="text-xs text-muted-foreground">错误</span>
          </div>
          <div className="text-2xl font-bold text-red-500 mt-1">{stats?.by_level?.error || 0}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <Globe className="w-4 h-4 text-blue-500" />
            <span className="text-xs text-muted-foreground">来源数</span>
          </div>
          <div className="text-2xl font-bold mt-1">{stats?.by_organ ? Object.keys(stats.by_organ).length : 0}</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索通知标题、内容、来源..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={levelFilter}
          onChange={(e) => setLevelFilter(e.target.value)}
        >
          <option value="">全部级别</option>
          <option value="info">信息</option>
          <option value="warning">警告</option>
          <option value="error">错误</option>
          <option value="success">成功</option>
        </select>
        {sources.length > 0 && (
          <select
            className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value)}
          >
            <option value="">全部来源</option>
            {sources.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        )}
        <label className="flex items-center gap-1.5 text-xs cursor-pointer">
          <input type="checkbox" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.target.checked)} className="rounded" />
          仅未读
        </label>
        <Button onClick={fetchNotifications} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
        <Button onClick={handleMarkAllRead} variant="secondary">
          <CheckCheck className="w-3.5 h-3.5" />全部已读
        </Button>
        <Button onClick={() => setShowForwardRules(true)} variant="secondary">
          <Settings className="w-3.5 h-3.5" />转发规则
        </Button>
        <Button onClick={() => setShowStats(true)} variant="secondary">
          <BarChart3 className="w-3.5 h-3.5" />统计
        </Button>
        <Button onClick={handleTest} variant="secondary">
          <Beaker className="w-3.5 h-3.5" />测试
        </Button>
        <Button onClick={handleClearAll} variant="chip-sm" className="flex items-center gap-1.5 bg-red-500/10 text-red-500 border border-red-500/20 hover:bg-red-500/20">
          <Trash2 className="w-3.5 h-3.5" />清空
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

      {/* Notifications list */}
      <div className="space-y-1">
        {filtered.map((notif) => {
          const config = LEVEL_CONFIG[notif.level] || LEVEL_CONFIG.info;
          const LevelIcon = config.icon;
          return (
            <div
              key={notif.id}
              className={`rounded-lg border p-3 transition-colors ${notif.read ? 'border-border bg-card hover:bg-muted/30' : 'border-primary/20 bg-primary/5 hover:bg-primary/10'}`}
            >
              <div className="flex items-start gap-3">
                <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${config.bg}`}>
                  <LevelIcon className={`w-4 h-4 ${config.color}`} />
                </div>
                <div className="min-w-0 flex-1 cursor-pointer" onClick={() => setSelectedNotif(notif)}>
                  <div className="flex items-center gap-2">
                    <h3 className="text-xs font-medium truncate">{notif.emoji} {notif.title}</h3>
                    {!notif.read && <span className="w-1.5 h-1.5 rounded-full bg-primary shrink-0" />}
                    <Badge variant="bare" className={`${config.bg} ${config.color}`}>{config.label}</Badge>
                  </div>
                  <p className="text-[11px] text-muted-foreground mt-0.5 line-clamp-2">{notif.body}</p>
                  <div className="flex items-center gap-3 mt-1 text-[10px] text-muted-foreground">
                    <span>{notif.source}</span>
                    {notif.organ && <span>器官: {notif.organ}</span>}
                    <span title={formatTime(notif.timestamp)}>{formatRelative(notif.timestamp)}</span>
                  </div>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  {!notif.read && (
                    <Button
                      onClick={() => handleMarkRead(notif.id)}
                      variant="icon-sm" className="hover:bg-muted"
                      title="标为已读"
                    >
                      <CheckCheck className="w-3 h-3 text-muted-foreground" />
                    </Button>
                  )}
                  <Button
                    onClick={() => { setForwardNotif(notif); setForwardChannel(''); }}
                    variant="icon-sm" className="hover:bg-muted"
                    title="转发到Echo"
                  >
                    <Send className="w-3 h-3 text-muted-foreground" />
                  </Button>
                  <Button
                    onClick={() => handleDismiss(notif.id)}
                    variant="icon-sm" className="hover:bg-muted"
                    title="关闭"
                  >
                    <X className="w-3 h-3 text-muted-foreground" />
                  </Button>
                </div>
              </div>
            </div>
          );
        })}
        {filtered.length === 0 && (
          <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
            <Bell className="w-10 h-10 mb-2 opacity-30" />
            <p className="text-xs">{search ? '没有匹配的通知' : '暂无通知'}</p>
          </div>
        )}
      </div>

      {/* Detail Dialog */}
      {selectedNotif && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setSelectedNotif(null)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4 max-h-[80vh] overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <div className="flex items-center gap-2">
                {(() => {
                  const config = LEVEL_CONFIG[selectedNotif.level] || LEVEL_CONFIG.info;
                  const LevelIcon = config.icon;
                  return <LevelIcon className={`w-4 h-4 ${config.color}`} />;
                })()}
                <h2 className="text-sm font-medium">{selectedNotif.emoji} {selectedNotif.title}</h2>
              </div>
              <Button onClick={() => setSelectedNotif(null)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 overflow-y-auto space-y-3 text-xs">
              <div className="p-3 rounded bg-muted/50">
                <p className="text-sm">{selectedNotif.body}</p>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div className="p-2 rounded bg-muted/30">
                  <div className="text-muted-foreground">来源</div>
                  <div className="font-medium mt-0.5">{selectedNotif.source}</div>
                </div>
                <div className="p-2 rounded bg-muted/30">
                  <div className="text-muted-foreground">器官</div>
                  <div className="font-medium mt-0.5">{selectedNotif.organ || '-'}</div>
                </div>
                <div className="p-2 rounded bg-muted/30">
                  <div className="text-muted-foreground">级别</div>
                  <div className="font-medium mt-0.5">{LEVEL_CONFIG[selectedNotif.level]?.label || selectedNotif.level}</div>
                </div>
                <div className="p-2 rounded bg-muted/30">
                  <div className="text-muted-foreground">时间</div>
                  <div className="font-medium mt-0.5">{formatTime(selectedNotif.timestamp)}</div>
                </div>
              </div>
              {selectedNotif.action_url && (
                <div className="p-2 rounded bg-muted/30">
                  <div className="text-muted-foreground">关联页面</div>
                  <div className="font-mono mt-0.5">{selectedNotif.action_url}</div>
                </div>
              )}
              {Object.keys(selectedNotif.metadata || {}).length > 0 && (
                <div>
                  <div className="text-muted-foreground mb-1">元数据</div>
                  <pre className="p-2 rounded bg-muted/30 text-[10px] overflow-x-auto">
                    {JSON.stringify(selectedNotif.metadata, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Forward Rules Dialog */}
      {showForwardRules && forwardRules && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowForwardRules(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4 max-h-[80vh] overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <div className="flex items-center gap-2">
                <Settings className="w-4 h-4 text-primary" />
                <h2 className="text-sm font-medium">Echo 转发规则</h2>
              </div>
              <Button onClick={() => setShowForwardRules(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 overflow-y-auto space-y-3">
              <div className="flex items-center justify-between p-3 rounded bg-muted/30">
                <div>
                  <div className="text-xs font-medium">全局转发</div>
                  <div className="text-[10px] text-muted-foreground">启用后自动转发通知到Echo通道</div>
                </div>
                <Button variant="bare"
                  onClick={() => handleToggleForward(!forwardRules.enabled)}
                  className={`relative w-10 h-5 rounded-full transition-colors ${forwardRules.enabled ? 'bg-green-500' : 'bg-muted'}`}
                >
                  <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${forwardRules.enabled ? 'left-5' : 'left-0.5'}`} />
                </Button>
              </div>
              <div className="text-xs text-muted-foreground">转发规则（通知级别 → Echo通道）</div>
              {Object.entries(forwardRules.rules).length === 0 ? (
                <div className="text-xs text-muted-foreground text-center py-4">暂无转发规则</div>
              ) : (
                <div className="space-y-2">
                  {Object.entries(forwardRules.rules).map(([level, channels]) => {
                    const config = LEVEL_CONFIG[level] || LEVEL_CONFIG.info;
                    return (
                      <div key={level} className="flex items-center gap-2 p-2 rounded border border-border">
                        <Badge variant="bare" className={`${config.bg} ${config.color}`}>{config.label}</Badge>
                        <span className="text-[10px] text-muted-foreground">→</span>
                        <div className="flex gap-1 flex-wrap">
                          {channels.map((ch) => (
                            <Badge variant="default" key={ch} className="font-mono">{ch}</Badge>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
              <div className="flex gap-2 pt-2">
                <Button onClick={handleTestForward} variant="secondary" className="gap-1">
                  <Beaker className="w-3 h-3" />发送测试转发
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Stats Dialog */}
      {showStats && stats && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowStats(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4 max-h-[80vh] overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <div className="flex items-center gap-2">
                <BarChart3 className="w-4 h-4 text-primary" />
                <h2 className="text-sm font-medium">通知统计</h2>
              </div>
              <Button onClick={() => setShowStats(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 overflow-y-auto space-y-4">
              <div>
                <div className="text-xs font-medium mb-2">按级别分布</div>
                <div className="space-y-1">
                  {Object.entries(stats.by_level || {}).sort((a, b) => b[1] - a[1]).map(([level, count]) => {
                    const config = LEVEL_CONFIG[level] || LEVEL_CONFIG.info;
                    const pct = stats.total > 0 ? (count / stats.total) * 100 : 0;
                    return (
                      <div key={level} className="flex items-center gap-2">
                        <Badge variant="bare" className={`${config.bg} ${config.color} w-12 text-center`}>{config.label}</Badge>
                        <div className="flex-1 bg-muted rounded-full h-2">
                          <div className={`h-2 rounded-full ${config.color.replace('text-', 'bg-')}`} style={{ width: `${pct}%` }} />
                        </div>
                        <span className="text-[10px] font-mono w-8 text-right">{count}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
              <div>
                <div className="text-xs font-medium mb-2">按来源分布</div>
                <div className="space-y-1">
                  {Object.entries(stats.by_organ || {}).sort((a, b) => b[1] - a[1]).slice(0, 15).map(([organ, count]) => {
                    const pct = stats.total > 0 ? (count / stats.total) * 100 : 0;
                    return (
                      <div key={organ} className="flex items-center gap-2">
                        <span className="text-[10px] w-20 truncate">{organ}</span>
                        <div className="flex-1 bg-muted rounded-full h-2">
                          <div className="h-2 rounded-full bg-primary" style={{ width: `${pct}%` }} />
                        </div>
                        <span className="text-[10px] font-mono w-8 text-right">{count}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Forward Dialog */}
      {forwardNotif && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setForwardNotif(null)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-sm mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">转发到 Echo 通道</h2>
              <Button onClick={() => setForwardNotif(null)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div className="text-xs text-muted-foreground">
                转发: {forwardNotif.emoji} {forwardNotif.title}
              </div>
              <div>
                <label className="text-xs text-muted-foreground">选择 Echo 通道</label>
                <select
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  value={forwardChannel}
                  onChange={(e) => setForwardChannel(e.target.value)}
                >
                  <option value="">选择通道...</option>
                  <option value="console">Console</option>
                  <option value="dingtalk">DingTalk</option>
                  <option value="telegram">Telegram</option>
                  <option value="webhook">Webhook</option>
                  <option value="email">Email</option>
                </select>
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setForwardNotif(null)} variant="secondary">取消</Button>
                <Button onClick={handleForward}
                  disabled={!forwardChannel || forwarding} variant="primary">
                  {forwarding ? <Loader2 className="w-3 h-3 animate-spin" /> : <Send className="w-3 h-3" />}
                  {forwarding ? '转发中...' : '转发'}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
