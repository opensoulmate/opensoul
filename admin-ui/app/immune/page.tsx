'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Plus, Trash2, RefreshCw, X, ShieldCheck, AlertTriangle,
  Eye, Ban, CheckCircle, Clock, Lock, Unlock, BarChart3, FileText,
  Zap, Globe, ShieldAlert, ShieldOff
} from 'lucide-react';
import { Badge, tone } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

interface Threat {
  id: string;
  source_ip: string;
  attack_type: string;
  threat_level: string;
  detail: string;
  timestamp: string;
}

interface AuditEntry {
  action: string;
  client_ip: string;
  endpoint: string;
  detail: string;
  risk_level: string;
  timestamp: string;
}

interface IPEntry {
  ip: string;
  reason: string;
  added_at: string;
  expires_at?: string;
}

type Tab = 'moderate' | 'ip' | 'audit' | 'intrusion' | 'rate';

export default function ImmunePage() {
  const [tab, setTab] = useState<Tab>('moderate');
  const [error, setError] = useState('');
  const [stats, setStats] = useState<any>(null);

  // Content moderation
  const [moderateText, setModerateText] = useState('');
  const [moderateResult, setModerateResult] = useState<any>(null);
  const [moderateLoading, setModerateLoading] = useState(false);

  // IP lists
  const [ipLists, setIpLists] = useState<{ blacklist: IPEntry[]; whitelist: IPEntry[] }>({ blacklist: [], whitelist: [] });
  const [ipForm, setIpForm] = useState({ ip: '', reason: '', ttl: '' });
  const [showAddIp, setShowAddIp] = useState(false);
  const [ipAction, setIpAction] = useState<'blacklist' | 'whitelist'>('blacklist');

  // Audit log
  const [auditLog, setAuditLog] = useState<AuditEntry[]>([]);
  const [auditFilter, setAuditFilter] = useState({ action: '', risk_level: '' });

  // Intrusion
  const [threats, setThreats] = useState<Threat[]>([]);
  const [blockedIps, setBlockedIps] = useState<string[]>([]);

  // Rate limit
  const [rateStats, setRateStats] = useState<any>(null);

  const fetchStats = useCallback(async () => {
    try {
      const data = await apiFetch('/api/immune/stats');
      setStats(data);
    } catch { /* silent */ }
  }, []);

  const fetchIpLists = useCallback(async () => {
    try {
      const data = await apiFetch('/api/immune/ip/lists');
      setIpLists(data);
    } catch (e: any) {
      setError(e.message);
    }
  }, []);

  const fetchAuditLog = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (auditFilter.action) params.set('action', auditFilter.action);
      if (auditFilter.risk_level) params.set('risk_level', auditFilter.risk_level);
      params.set('limit', '100');
      const data = await apiFetch(`/api/immune/audit/log?${params}`);
      setAuditLog(Array.isArray(data.entries) ? data.entries : []);
    } catch (e: any) {
      setError(e.message);
    }
  }, [auditFilter]);

  const fetchThreats = useCallback(async () => {
    try {
      const data = await apiFetch('/api/immune/intrusion/threats?limit=50');
      setThreats(Array.isArray(data.threats) ? data.threats : []);
    } catch (e: any) {
      setError(e.message);
    }
  }, []);

  const fetchBlockedIps = useCallback(async () => {
    try {
      const data = await apiFetch('/api/immune/intrusion/blocked');
      setBlockedIps(Array.isArray(data.blocked_ips) ? data.blocked_ips : []);
    } catch { /* silent */ }
  }, []);

  const fetchRateStats = useCallback(async () => {
    try {
      const data = await apiFetch('/api/immune/rate-limit/stats');
      setRateStats(data);
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    fetchStats();
    fetchIpLists();
    fetchAuditLog();
    fetchThreats();
    fetchBlockedIps();
    fetchRateStats();
  }, [fetchStats, fetchIpLists, fetchAuditLog, fetchThreats, fetchBlockedIps, fetchRateStats]);

  const handleModerate = async () => {
    if (!moderateText.trim()) return;
    try {
      setModerateLoading(true);
      const data = await apiFetch('/api/immune/moderate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: moderateText }),
      });
      setModerateResult(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setModerateLoading(false);
    }
  };

  const handleAddIp = async () => {
    try {
      const endpoint = ipAction === 'blacklist' ? '/api/immune/ip/blacklist' : '/api/immune/ip/whitelist';
      const body: any = { ip: ipForm.ip, reason: ipForm.reason };
      if (ipAction === 'blacklist' && ipForm.ttl) body.ttl_seconds = parseInt(ipForm.ttl);
      await apiFetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      setShowAddIp(false);
      setIpForm({ ip: '', reason: '', ttl: '' });
      await fetchIpLists();
      await fetchStats();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleRemoveIp = async (ip: string, list: 'blacklist' | 'whitelist') => {
    try {
      await apiFetch(`/api/immune/ip/${list}/${ip}`, { method: 'DELETE' });
      await fetchIpLists();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleBlockIp = async (ip: string) => {
    try {
      await apiFetch('/api/immune/intrusion/block', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ip }),
      });
      await fetchBlockedIps();
      await fetchIpLists();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleUnblockIp = async (ip: string) => {
    try {
      await apiFetch(`/api/immune/intrusion/block/${ip}`, { method: 'DELETE' });
      await fetchBlockedIps();
      await fetchIpLists();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleResetRateLimit = async () => {
    try {
      await apiFetch('/api/immune/rate-limit/reset', { method: 'POST' });
      await fetchRateStats();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const riskColor = (level: string) => {
    switch (level) {
      case 'critical': return tone('destructive');
      case 'high': return tone('destructive');
      case 'medium': return tone('warning');
      case 'low': return tone('warning');
      default: return tone('muted');
    }
  };

  const tabs: { key: Tab; label: string; icon: any }[] = [
    { key: 'moderate', label: '内容审核', icon: Eye },
    { key: 'ip', label: 'IP管控', icon: Globe },
    { key: 'intrusion', label: '入侵检测', icon: ShieldAlert },
    { key: 'audit', label: '审计日志', icon: FileText },
    { key: 'rate', label: '限流', icon: Zap },
  ];

  return (
    <div className="space-y-4">
      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-500/10 text-red-500 text-xs">
          <AlertTriangle className="w-3.5 h-3.5" />{error}
          <Button variant="bare" onClick={() => setError('')} className="ml-auto"><X className="w-3.5 h-3.5" /></Button>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{stats?.rate_limiter?.total_keys ?? 0}</div>
          <div className="text-xs text-muted-foreground">限流键数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-red-500">{stats?.audit?.total ?? 0}</div>
          <div className="text-xs text-muted-foreground">审计记录</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-amber-500">{ipLists.blacklist.length}</div>
          <div className="text-xs text-muted-foreground">黑名单IP</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-emerald-600">{ipLists.whitelist.length}</div>
          <div className="text-xs text-muted-foreground">白名单IP</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-violet-500">{threats.length}</div>
          <div className="text-xs text-muted-foreground">威胁记录</div>
        </div>
      </div>

      {/* Tab Bar */}
      <div className="flex items-center gap-1 p-1 bg-muted rounded-lg w-fit overflow-x-auto">
        {tabs.map(t => (
          <Button variant="bare"
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex items-center gap-1.5 px-3 py-2 text-xs lg:text-sm rounded-lg transition-colors whitespace-nowrap ${tab === t.key ? 'bg-primary/10 text-primary font-medium' : 'text-muted-foreground hover:text-foreground'}`}
          >
            <t.icon className="w-3.5 h-3.5" />{t.label}
          </Button>
        ))}
      </div>

      {/* Content Moderation Tab */}
      {tab === 'moderate' && (
        <div className="space-y-4">
          <div className="rounded-lg border border-border bg-card p-4">
            <h3 className="text-sm font-medium mb-3">内容安全扫描</h3>
            <textarea
              className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 h-28 resize-none"
              value={moderateText} onChange={e => setModerateText(e.target.value)}
              placeholder="输入要扫描的文本内容（PII检测、敏感数据检测等）..."
            />
            <div className="flex items-center gap-2 mt-3">
              <Button onClick={handleModerate} disabled={moderateLoading || !moderateText.trim()} variant="chip-sm" className="flex items-center gap-1.5 bg-primary/10 text-primary border border-border hover:bg-primary/20 disabled:opacity-50">
                {moderateLoading ? <Zap className="w-3.5 h-3.5 animate-spin" /> : <Eye className="w-3.5 h-3.5" />}
                {moderateLoading ? '扫描中...' : '开始扫描'}
              </Button>
            </div>
            {moderateResult && (
              <div className="mt-4 p-3 rounded-lg bg-muted/50 space-y-3">
                <div className="flex items-center gap-2">
                  {moderateResult.is_safe ? (
                    <><CheckCircle className="w-5 h-5 text-emerald-600" /><span className="text-sm font-medium text-emerald-600">安全</span></>
                  ) : (
                    <><AlertTriangle className="w-5 h-5 text-red-500" /><span className="text-sm font-medium text-red-500">发现风险</span></>
                  )}
                  <Badge className={`${riskColor(moderateResult.risk_level)}`}>{moderateResult.risk_level}</Badge>
                </div>
                {moderateResult.findings?.length > 0 && (
                  <div className="space-y-1.5">
                    {moderateResult.findings.map((f: any, i: number) => (
                      <div key={i} className="flex items-center gap-2 text-xs">
                        <span className={`px-1.5 py-0.5 rounded ${riskColor(f.risk)}`}>{f.risk}</span>
                        <span className="text-muted-foreground">{f.type}</span>
                        <span>{f.label}</span>
                      </div>
                    ))}
                  </div>
                )}
                {moderateResult.redacted_text && (
                  <div>
                    <div className="text-xs text-muted-foreground mb-1">脱敏结果:</div>
                    <div className="text-xs bg-card p-2 rounded border border-border font-mono break-all">{moderateResult.redacted_text}</div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* IP Control Tab */}
      {tab === 'ip' && (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <Button onClick={() => { setIpAction('blacklist'); setShowAddIp(true); }} variant="chip-sm" className="flex items-center gap-1.5 bg-red-500/10 text-red-500 border border-border hover:bg-red-500/20">
              <Ban className="w-3.5 h-3.5" />加入黑名单
            </Button>
            <Button onClick={() => { setIpAction('whitelist'); setShowAddIp(true); }} variant="chip-sm" className="flex items-center gap-1.5 bg-emerald-500/10 text-emerald-500 border border-border hover:bg-emerald-500/20">
              <CheckCircle className="w-3.5 h-3.5" />加入白名单
            </Button>
            <Button onClick={fetchIpLists} variant="secondary">
              <RefreshCw className="w-3.5 h-3.5" />刷新
            </Button>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Blacklist */}
            <div className="rounded-lg border border-border overflow-hidden">
              <div className="bg-red-500/5 px-4 py-2 border-b border-border">
                <h3 className="text-sm font-medium flex items-center gap-1.5"><Ban className="w-4 h-4 text-red-500" />黑名单 ({ipLists.blacklist.length})</h3>
              </div>
              <div className="max-h-[400px] overflow-auto">
                {ipLists.blacklist.length === 0 ? (
                  <div className="px-4 py-6 text-center text-xs text-muted-foreground">暂无黑名单</div>
                ) : ipLists.blacklist.map(entry => (
                  <div key={entry.ip} className="flex items-center justify-between px-4 py-2 border-b border-border hover:bg-muted/30">
                    <div>
                      <div className="text-sm font-mono">{entry.ip}</div>
                      <div className="text-xs text-muted-foreground">{entry.reason || '—'}</div>
                    </div>
                    <Button variant="bare" onClick={() => handleRemoveIp(entry.ip, 'blacklist')} className="p-1 text-muted-foreground hover:text-red-500">
                      <Trash2 className="w-3.5 h-3.5" />
                    </Button>
                  </div>
                ))}
              </div>
            </div>

            {/* Whitelist */}
            <div className="rounded-lg border border-border overflow-hidden">
              <div className="bg-emerald-500/5 px-4 py-2 border-b border-border">
                <h3 className="text-sm font-medium flex items-center gap-1.5"><CheckCircle className="w-4 h-4 text-emerald-600" />白名单 ({ipLists.whitelist.length})</h3>
              </div>
              <div className="max-h-[400px] overflow-auto">
                {ipLists.whitelist.length === 0 ? (
                  <div className="px-4 py-6 text-center text-xs text-muted-foreground">暂无白名单</div>
                ) : ipLists.whitelist.map(entry => (
                  <div key={entry.ip} className="flex items-center justify-between px-4 py-2 border-b border-border hover:bg-muted/30">
                    <div>
                      <div className="text-sm font-mono">{entry.ip}</div>
                      <div className="text-xs text-muted-foreground">{entry.reason || '—'}</div>
                    </div>
                    <Button variant="bare" onClick={() => handleRemoveIp(entry.ip, 'whitelist')} className="p-1 text-muted-foreground hover:text-red-500">
                      <Trash2 className="w-3.5 h-3.5" />
                    </Button>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Intrusion Tab */}
      {tab === 'intrusion' && (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <Button onClick={fetchThreats} variant="secondary">
              <RefreshCw className="w-3.5 h-3.5" />刷新
            </Button>
          </div>

          {/* Blocked IPs */}
          {blockedIps.length > 0 && (
            <div className="rounded-lg border border-border bg-card p-4">
              <h3 className="text-sm font-medium mb-2 flex items-center gap-1.5"><Lock className="w-4 h-4 text-red-500" />自动封锁的IP</h3>
              <div className="flex flex-wrap gap-2">
                {blockedIps.map(ip => (
                  <div key={ip} className="flex items-center gap-1.5 px-2 py-1 bg-red-500/10 rounded text-xs">
                    <span className="font-mono">{ip}</span>
                    <Button variant="bare" onClick={() => handleUnblockIp(ip)} className="text-muted-foreground hover:text-emerald-600"><Unlock className="w-3 h-3" /></Button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Threats */}
          <div className="rounded-lg border border-border overflow-hidden">
            <table className="w-full text-xs lg:text-sm min-w-[500px]">
              <thead>
                <tr className="border-b border-border bg-muted/30">
                  <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">IP</th>
                  <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">攻击类型</th>
                  <th className="text-center px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">威胁等级</th>
                  <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">详情</th>
                  <th className="text-left px-2 lg:px-4 py-2.5 font-medium w-32 text-muted-foreground">时间</th>
                  <th className="text-right px-2 lg:px-4 py-2.5 font-medium w-20 text-muted-foreground">操作</th>
                </tr>
              </thead>
              <tbody>
                {threats.map((t, i) => (
                  <tr key={t.id || i} className="border-b border-border last:border-0 hover:bg-muted/30">
                    <td className="px-2 lg:px-4 py-2.5 font-mono text-xs">{t.source_ip}</td>
                    <td className="px-2 lg:px-4 py-2.5">{t.attack_type}</td>
                    <td className="px-2 lg:px-4 py-2.5 text-center">
                      <Badge className={`${riskColor(t.threat_level)}`}>{t.threat_level}</Badge>
                    </td>
                    <td className="px-2 lg:px-4 py-2.5 text-xs text-muted-foreground truncate max-w-[200px]">{t.detail}</td>
                    <td className="px-2 lg:px-4 py-2.5 text-xs text-muted-foreground">{t.timestamp ? new Date(t.timestamp).toLocaleString() : '—'}</td>
                    <td className="px-2 lg:px-4 py-2.5 text-right">
                      <Button variant="bare" onClick={() => handleBlockIp(t.source_ip)} className="p-1 text-muted-foreground hover:text-red-500" title="封锁IP">
                        <Ban className="w-3.5 h-3.5" />
                      </Button>
                    </td>
                  </tr>
                ))}
                {threats.length === 0 && (
                  <tr><td colSpan={6} className="px-2 lg:px-4 py-8 text-center text-muted-foreground">暂无威胁记录</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Audit Tab */}
      {tab === 'audit' && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <select className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={auditFilter.action} onChange={e => setAuditFilter({ ...auditFilter, action: e.target.value })}>
              <option value="">全部操作</option>
              <option value="content_blocked">内容拦截</option>
              <option value="rate_limited">限流</option>
              <option value="ip_blocked">IP封锁</option>
              <option value="config_change">配置变更</option>
            </select>
            <select className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={auditFilter.risk_level} onChange={e => setAuditFilter({ ...auditFilter, risk_level: e.target.value })}>
              <option value="">全部等级</option>
              <option value="critical">严重</option>
              <option value="high">高</option>
              <option value="medium">中</option>
              <option value="low">低</option>
            </select>
            <Button onClick={fetchAuditLog} variant="secondary">
              <RefreshCw className="w-3.5 h-3.5" />刷新
            </Button>
          </div>

          <div className="rounded-lg border border-border overflow-hidden">
            <table className="w-full text-xs lg:text-sm min-w-[500px]">
              <thead>
                <tr className="border-b border-border bg-muted/30">
                  <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">操作</th>
                  <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">IP</th>
                  <th className="text-center px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">风险</th>
                  <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">详情</th>
                  <th className="text-left px-2 lg:px-4 py-2.5 font-medium w-32 text-muted-foreground">时间</th>
                </tr>
              </thead>
              <tbody>
                {auditLog.map((entry, i) => (
                  <tr key={i} className="border-b border-border last:border-0 hover:bg-muted/30">
                    <td className="px-2 lg:px-4 py-2.5 text-xs font-medium">{entry.action}</td>
                    <td className="px-2 lg:px-4 py-2.5 text-xs font-mono">{entry.client_ip || '—'}</td>
                    <td className="px-2 lg:px-4 py-2.5 text-center">
                      <Badge className={`${riskColor(entry.risk_level)}`}>{entry.risk_level}</Badge>
                    </td>
                    <td className="px-2 lg:px-4 py-2.5 text-xs text-muted-foreground truncate max-w-[300px]">{entry.detail || '—'}</td>
                    <td className="px-2 lg:px-4 py-2.5 text-xs text-muted-foreground">{entry.timestamp ? new Date(entry.timestamp).toLocaleString() : '—'}</td>
                  </tr>
                ))}
                {auditLog.length === 0 && (
                  <tr><td colSpan={5} className="px-2 lg:px-4 py-8 text-center text-muted-foreground">暂无审计记录</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Rate Limit Tab */}
      {tab === 'rate' && (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <Button onClick={fetchRateStats} variant="secondary">
              <RefreshCw className="w-3.5 h-3.5" />刷新
            </Button>
            <Button onClick={handleResetRateLimit} variant="chip-sm" className="flex items-center gap-1.5 bg-amber-500/10 text-amber-500 border border-border hover:bg-amber-500/20">
              <Zap className="w-3.5 h-3.5" />重置计数
            </Button>
          </div>

          {rateStats && (
            <div className="rounded-lg border border-border bg-card p-4">
              <h3 className="text-sm font-medium mb-3">限流统计</h3>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                <div><span className="text-muted-foreground">总键数: </span><span className="font-medium">{rateStats.total_keys ?? 0}</span></div>
                <div><span className="text-muted-foreground">活跃键: </span><span className="font-medium">{rateStats.active_keys ?? 0}</span></div>
                {rateStats.config && (
                  <>
                    <div><span className="text-muted-foreground">每分钟限制: </span><span className="font-medium">{rateStats.config.requests_per_minute}</span></div>
                    <div><span className="text-muted-foreground">每小时限制: </span><span className="font-medium">{rateStats.config.requests_per_hour}</span></div>
                  </>
                )}
              </div>
              {rateStats.keys && Object.keys(rateStats.keys).length > 0 && (
                <div className="mt-3">
                  <div className="text-xs text-muted-foreground mb-2">活跃限流键:</div>
                  <div className="space-y-1">
                    {Object.entries(rateStats.keys).slice(0, 20).map(([key, counts]: [string, any]) => (
                      <div key={key} className="flex items-center gap-2 text-xs">
                        <span className="font-mono w-32 truncate">{key}</span>
                        <span className="text-muted-foreground">分钟: {counts.minute_count ?? 0}</span>
                        <span className="text-muted-foreground">小时: {counts.hour_count ?? 0}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Add IP Dialog */}
      {showAddIp && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowAddIp(false)}>
          <div className="bg-card rounded-lg border border-border p-6 max-w-md w-full mx-4" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-medium">{ipAction === 'blacklist' ? '加入黑名单' : '加入白名单'}</h2>
              <Button variant="bare" onClick={() => setShowAddIp(false)}><X className="w-4 h-4 text-muted-foreground" /></Button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">IP地址 *</label>
                <input className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 font-mono" value={ipForm.ip} onChange={e => setIpForm({ ...ipForm, ip: e.target.value })} placeholder="192.168.1.1" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">原因</label>
                <input className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={ipForm.reason} onChange={e => setIpForm({ ...ipForm, reason: e.target.value })} placeholder="原因描述" />
              </div>
              {ipAction === 'blacklist' && (
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">过期时间（秒，空=永久）</label>
                  <input type="number" className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={ipForm.ttl} onChange={e => setIpForm({ ...ipForm, ttl: e.target.value })} placeholder="3600" />
                </div>
              )}
            </div>
            <div className="flex justify-end gap-2 mt-6">
              <Button onClick={() => setShowAddIp(false)} variant="chip-sm" className="bg-muted border border-border">取消</Button>
              <Button onClick={handleAddIp} disabled={!ipForm.ip.trim()} variant="chip-sm" className={`flex items-center gap-1.5 disabled:opacity-50  ${ipAction === 'blacklist' ? 'bg-red-500 text-white' : 'bg-emerald-600 text-white'}`}>
                {ipAction === 'blacklist' ? <Ban className="w-3.5 h-3.5" /> : <CheckCircle className="w-3.5 h-3.5" />}
                确认
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
