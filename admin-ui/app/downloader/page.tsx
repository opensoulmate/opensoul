'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Download, RefreshCw, X, Play, Pause, Trash2, Search, Package,
  HardDrive, Settings, Loader2, AlertCircle, CheckCircle, Server,
  ArrowDownToLine, FolderOpen, Zap, Clock, Activity, ChevronDown, ChevronRight,
} from 'lucide-react';
import { Badge, tone } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

// ── Types ──────────────────────────────────────────────────────

interface Plugin {
  id: string;
  name: string;
  description: string;
  version: string;
  status: string;
  supports_resume: boolean;
  supports_p2p: boolean;
  priority: number;
}

interface DownloadTask {
  id: string;
  url: string;
  dest: string;
  status: string;
  total_bytes: number;
  downloaded_bytes: number;
  speed: number;
  eta: number;
  progress_pct: number;
  segments: number;
  supports_resume: boolean;
  error: string | null;
  plugin: string;
  added_at: string;
  started_at: string | null;
  completed_at: string | null;
}

interface CacheFile {
  name: string;
  path: string;
  size: number;
  modified: number;
}

interface DownloadConfig {
  threads: number;
  speed_limit: number;
  download_dir: string;
  proxy: string | null;
}

type Tab = 'tasks' | 'plugins' | 'cache' | 'config';

const STATUS_COLORS: Record<string, string> = {
  pending: tone('warning'),
  connecting: `${tone('info')} animate-pulse`,
  downloading: tone('success'),
  paused: tone('warning'),
  done: tone('success'),
  error: tone('destructive'),
  cancelled: tone('muted'),
  installed: tone('success'),
  available: tone('info'),
  unavailable: tone('muted'),
};

// ── Helpers ────────────────────────────────────────────────────

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return (bytes / Math.pow(k, i)).toFixed(1) + ' ' + sizes[i];
}

function formatSpeed(bytesPerSec: number): string {
  if (!bytesPerSec) return '—';
  return formatBytes(bytesPerSec) + '/s';
}

function formatEta(seconds: number): string {
  if (!seconds || seconds <= 0) return '—';
  if (seconds < 60) return `${Math.floor(seconds)}秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}分${Math.floor(seconds % 60)}秒`;
  return `${Math.floor(seconds / 3600)}时${Math.floor((seconds % 3600) / 60)}分`;
}

function timeAgo(ts: number): string {
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`;
  return `${Math.floor(diff / 86400)}天前`;
}

// ── Main Page ──────────────────────────────────────────────────

export default function DownloaderPage() {
  const [tab, setTab] = useState<Tab>('tasks');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Plugins
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [pluginsLoading, setPluginsLoading] = useState(false);

  // Tasks
  const [tasks, setTasks] = useState<DownloadTask[]>([]);
  const [tasksLoading, setTasksLoading] = useState(false);

  // Cache
  const [cacheFiles, setCacheFiles] = useState<CacheFile[]>([]);
  const [cacheTotalSize, setCacheTotalSize] = useState(0);
  const [cacheLoading, setCacheLoading] = useState(false);

  // Config
  const [config, setConfig] = useState<DownloadConfig | null>(null);
  const [configLoading, setConfigLoading] = useState(false);
  const [editConfig, setEditConfig] = useState({ key: '', value: '' });

  // New download
  const [showNewDownload, setShowNewDownload] = useState(false);
  const [newUrl, setNewUrl] = useState('');
  const [newDest, setNewDest] = useState('');
  const [downloading, setDownloading] = useState(false);

  // Auto-refresh for active tasks
  const refreshRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Fetch ──────────────────────────────────────────────────

  const fetchPlugins = useCallback(async () => {
    try {
      setPluginsLoading(true);
      const data = await apiFetch('/api/download/plugins');
      setPlugins(data.plugins || []);
    } catch (e: any) {
      // silent
    } finally {
      setPluginsLoading(false);
    }
  }, []);

  const fetchTasks = useCallback(async () => {
    try {
      setTasksLoading(true);
      const data = await apiFetch('/api/download/tasks');
      setTasks(data.tasks || []);
    } catch (e: any) {
      // silent
    } finally {
      setTasksLoading(false);
    }
  }, []);

  const fetchCache = useCallback(async () => {
    try {
      setCacheLoading(true);
      const data = await apiFetch('/api/download/cache');
      setCacheFiles(data.files || []);
      setCacheTotalSize(data.total_size || 0);
    } catch (e: any) {
      // silent
    } finally {
      setCacheLoading(false);
    }
  }, []);

  const fetchConfig = useCallback(async () => {
    try {
      setConfigLoading(true);
      const data = await apiFetch('/api/download/config');
      setConfig(data);
    } catch (e: any) {
      // silent
    } finally {
      setConfigLoading(false);
    }
  }, []);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    await Promise.all([fetchPlugins(), fetchTasks(), fetchCache(), fetchConfig()]);
    setLoading(false);
  }, [fetchPlugins, fetchTasks, fetchCache, fetchConfig]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  // Auto-refresh tasks every 3s when there are active downloads
  useEffect(() => {
    const hasActive = tasks.some((t) => ['downloading', 'connecting', 'pending'].includes(t.status));
    if (hasActive && !refreshRef.current) {
      refreshRef.current = setInterval(fetchTasks, 3000);
    } else if (!hasActive && refreshRef.current) {
      clearInterval(refreshRef.current);
      refreshRef.current = null;
    }
    return () => {
      if (refreshRef.current) {
        clearInterval(refreshRef.current);
        refreshRef.current = null;
      }
    };
  }, [tasks, fetchTasks]);

  // ── Actions ────────────────────────────────────────────────

  const handleStartDownload = async () => {
    if (!newUrl) return;
    try {
      setDownloading(true);
      await apiFetch('/api/download/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: newUrl, dest: newDest || undefined, resume: true }),
      });
      setShowNewDownload(false);
      setNewUrl('');
      setNewDest('');
      fetchTasks();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setDownloading(false);
    }
  };

  const handlePause = async (taskId: string) => {
    try {
      await apiFetch(`/api/download/tasks/${taskId}/pause`, { method: 'POST' });
      fetchTasks();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleResume = async (taskId: string) => {
    try {
      await apiFetch(`/api/download/tasks/${taskId}/resume`, { method: 'POST' });
      fetchTasks();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleCancel = async (taskId: string) => {
    if (!confirm('确定要取消此下载？')) return;
    try {
      await apiFetch(`/api/download/tasks/${taskId}`, { method: 'DELETE' });
      fetchTasks();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleInstallPlugin = async (pluginId: string) => {
    try {
      await apiFetch(`/api/download/plugins/${pluginId}/install`, { method: 'POST' });
      fetchPlugins();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleUpdatePlugin = async (pluginId: string) => {
    try {
      await apiFetch(`/api/download/plugins/${pluginId}/update`, { method: 'POST' });
      fetchPlugins();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleUpdateAll = async () => {
    try {
      await apiFetch('/api/download/plugins/update-all', { method: 'POST' });
      fetchPlugins();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleDeleteCacheFile = async (filename: string) => {
    if (!confirm(`确定要删除缓存文件「${filename}」？`)) return;
    try {
      await apiFetch(`/api/download/cache/${filename}`, { method: 'DELETE' });
      fetchCache();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleClearCache = async () => {
    if (!confirm('确定要清空所有下载缓存？')) return;
    try {
      await apiFetch('/api/download/cache', { method: 'DELETE' });
      fetchCache();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleSetConfig = async () => {
    if (!editConfig.key) return;
    try {
      await apiFetch('/api/download/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: editConfig.key, value: editConfig.value }),
      });
      setEditConfig({ key: '', value: '' });
      fetchConfig();
    } catch (e: any) {
      setError(e.message);
    }
  };

  // ── Stats ──────────────────────────────────────────────────

  const activeTasks = tasks.filter((t) => ['downloading', 'connecting', 'pending'].includes(t.status));
  const completedTasks = tasks.filter((t) => t.status === 'done');
  const installedPlugins = plugins.filter((p) => p.status === 'installed');

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
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><Activity className="w-3.5 h-3.5" />活跃下载</div>
          <div className="text-lg font-bold text-emerald-600">{activeTasks.length}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><CheckCircle className="w-3.5 h-3.5" />已完成</div>
          <div className="text-lg font-bold">{completedTasks.length}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><Package className="w-3.5 h-3.5" />插件</div>
          <div className="text-lg font-bold">{installedPlugins.length}/{plugins.length}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><HardDrive className="w-3.5 h-3.5" />缓存</div>
          <div className="text-lg font-bold">{formatBytes(cacheTotalSize)}</div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-border">
        {([
          { key: 'tasks', label: '下载任务', icon: Download },
          { key: 'plugins', label: '插件管理', icon: Package },
          { key: 'cache', label: '缓存管理', icon: HardDrive },
          { key: 'config', label: '引擎配置', icon: Settings },
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
            {t.key === 'tasks' && activeTasks.length > 0 && (
              <Badge variant="success" className="ml-1">{activeTasks.length}</Badge>
            )}
          </Button>
        ))}
        <div className="flex-1" />
        <Button onClick={() => setShowNewDownload(true)} variant="primary" className="mb-1">
          <ArrowDownToLine className="w-3 h-3" />新建下载
        </Button>
        <Button onClick={fetchAll} variant="secondary" className="mb-1">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
      </div>

      {/* ── Tasks Tab ── */}
      {tab === 'tasks' && (
        <div className="space-y-2">
          {tasks.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
              <Download className="w-12 h-12 mb-3 opacity-20" />
              <p className="text-sm">暂无下载任务</p>
              <p className="text-xs mt-1">点击「新建下载」开始</p>
            </div>
          ) : (
            tasks.map((task) => (
              <div key={task.id} className="rounded-lg border border-border bg-card p-3">
                <div className="flex items-start gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <Badge variant="bare" className={`${STATUS_COLORS[task.status] || 'bg-muted text-muted-foreground'}`}>
                        {task.status}
                      </Badge>
                      {task.plugin && (
                        <Badge variant="muted">{task.plugin}</Badge>
                      )}
                      {task.segments > 1 && (
                        <Badge variant="default">{task.segments}线程</Badge>
                      )}
                    </div>
                    <p className="text-xs font-mono truncate text-muted-foreground">{task.url}</p>
                    <p className="text-[10px] text-muted-foreground mt-0.5">
                      <FolderOpen className="w-3 h-3 inline mr-1" />{task.dest}
                    </p>

                    {/* Progress Bar */}
                    {['downloading', 'connecting', 'pending'].includes(task.status) && (
                      <div className="mt-2">
                        <div className="flex items-center justify-between text-[10px] text-muted-foreground mb-1">
                          <span>{formatBytes(task.downloaded_bytes)} / {formatBytes(task.total_bytes)}</span>
                          <span>{task.progress_pct.toFixed(1)}%</span>
                        </div>
                        <div className="w-full h-1.5 bg-muted rounded-full overflow-hidden">
                          <div
                            className="h-full bg-primary rounded-full transition-all duration-300"
                            style={{ width: `${Math.min(task.progress_pct, 100)}%` }}
                          />
                        </div>
                        <div className="flex items-center gap-3 mt-1 text-[10px] text-muted-foreground">
                          <span>{formatSpeed(task.speed)}</span>
                          <span>ETA: {formatEta(task.eta)}</span>
                        </div>
                      </div>
                    )}

                    {task.status === 'done' && (
                      <div className="mt-1 text-[10px] text-emerald-600">
                        <CheckCircle className="w-3 h-3 inline mr-1" />
                        {formatBytes(task.total_bytes)} · 完成于 {task.completed_at ? new Date(task.completed_at).toLocaleString('zh-CN') : '—'}
                      </div>
                    )}

                    {task.error && (
                      <div className="mt-1 text-[10px] text-red-500">
                        <AlertCircle className="w-3 h-3 inline mr-1" />{task.error}
                      </div>
                    )}
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-1 shrink-0">
                    {task.status === 'downloading' && (
                      <Button onClick={() => handlePause(task.id)} variant="icon-md" className="hover:bg-amber-500/10" title="暂停">
                        <Pause className="w-4 h-4 text-amber-500" />
                      </Button>
                    )}
                    {task.status === 'paused' && (
                      <Button onClick={() => handleResume(task.id)} variant="icon-md" className="hover:bg-emerald-500/10" title="继续">
                        <Play className="w-4 h-4 text-emerald-500" />
                      </Button>
                    )}
                    {!['done', 'cancelled'].includes(task.status) && (
                      <Button onClick={() => handleCancel(task.id)} variant="icon-md" className="hover:bg-red-500/10" title="取消">
                        <Trash2 className="w-4 h-4 text-red-500" />
                      </Button>
                    )}
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* ── Plugins Tab ── */}
      {tab === 'plugins' && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-xs text-muted-foreground">{plugins.length} 个插件 · {installedPlugins.length} 已安装</p>
            <Button
              onClick={handleUpdateAll}
              variant="chip-sm" className="flex items-center gap-1 bg-blue-500/10 text-blue-500 border border-blue-500/20 hover:bg-blue-500/20"
            >
              <RefreshCw className="w-3 h-3" />全部更新
            </Button>
          </div>

          {pluginsLoading ? (
            <div className="text-xs text-muted-foreground text-center py-8">加载中...</div>
          ) : plugins.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <Package className="w-10 h-10 mb-2 opacity-20" />
              <p className="text-xs">暂无下载插件</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {plugins.map((plugin) => (
                <div key={plugin.id} className="rounded-lg border border-border bg-card p-3">
                  <div className="flex items-start justify-between">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <h3 className="text-sm font-medium">{plugin.name}</h3>
                        <Badge variant="bare" className={`${STATUS_COLORS[plugin.status] || 'bg-muted text-muted-foreground'}`}>
                          {plugin.status}
                        </Badge>
                      </div>
                      <p className="text-[10px] text-muted-foreground mt-1">{plugin.description}</p>
                      <div className="flex items-center gap-2 mt-2">
                        <Badge variant="muted">v{plugin.version}</Badge>
                        <span className="text-[10px] text-muted-foreground">优先级: {plugin.priority}</span>
                        {plugin.supports_resume && (
                          <Badge variant="success">续传</Badge>
                        )}
                        {plugin.supports_p2p && (
                          <Badge variant="accent">P2P</Badge>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-1 shrink-0 ml-2">
                      {plugin.status !== 'installed' && (
                        <Button onClick={() => handleInstallPlugin(plugin.id)} variant="primary" className="py-1 text-[10px]">
                          <Download className="w-3 h-3" />安装
                        </Button>
                      )}
                      {plugin.status === 'installed' && (
                        <Button
                          onClick={() => handleUpdatePlugin(plugin.id)}
                          variant="chip-xs" className="flex items-center gap-1 bg-blue-500/10 text-blue-500 hover:bg-blue-500/20"
                        >
                          <RefreshCw className="w-3 h-3" />更新
                        </Button>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Cache Tab ── */}
      {tab === 'cache' && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-xs text-muted-foreground">{cacheFiles.length} 个文件 · 总计 {formatBytes(cacheTotalSize)}</p>
            <Button
              onClick={handleClearCache}
              disabled={cacheFiles.length === 0}
              variant="chip-sm" className="flex items-center gap-1 bg-red-500/10 text-red-500 border border-red-500/20 hover:bg-red-500/20 disabled:opacity-50"
            >
              <Trash2 className="w-3 h-3" />清空缓存
            </Button>
          </div>

          {cacheLoading ? (
            <div className="text-xs text-muted-foreground text-center py-8">加载中...</div>
          ) : cacheFiles.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <HardDrive className="w-10 h-10 mb-2 opacity-20" />
              <p className="text-xs">缓存为空</p>
            </div>
          ) : (
            <div className="space-y-1">
              {cacheFiles.map((file) => (
                <div key={file.name} className="flex items-center gap-3 rounded border border-border p-2.5 hover:bg-muted/30 transition-colors">
                  <HardDrive className="w-4 h-4 text-muted-foreground shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-medium truncate">{file.name}</p>
                    <p className="text-[10px] text-muted-foreground">{formatBytes(file.size)} · {timeAgo(file.modified)}</p>
                  </div>
                  <Button
                    onClick={() => handleDeleteCacheFile(file.name)}
                    variant="icon-md" className="hover:bg-red-500/10 shrink-0"
                    title="删除"
                  >
                    <Trash2 className="w-3.5 h-3.5 text-red-500" />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Config Tab ── */}
      {tab === 'config' && (
        <div className="max-w-lg space-y-4">
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-center gap-2 mb-4">
              <Settings className="w-4 h-4 text-primary" />
              <h3 className="text-sm font-medium">下载引擎配置</h3>
            </div>
            {configLoading ? (
              <div className="text-xs text-muted-foreground">加载中...</div>
            ) : config ? (
              <div className="space-y-2">
                {Object.entries(config).map(([key, value]) => (
                  <div key={key} className="flex items-center justify-between rounded border border-border p-2">
                    <span className="text-xs font-mono text-muted-foreground">{key}</span>
                    <span className="text-xs font-medium">{value === null ? '—' : String(value)}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-xs text-muted-foreground">无法加载配置</div>
            )}
          </div>

          <div className="rounded-lg border border-border bg-card p-4">
            <h3 className="text-xs font-medium text-muted-foreground mb-3">修改配置</h3>
            <div className="flex items-end gap-2">
              <div className="flex-1">
                <label className="text-xs text-muted-foreground">Key</label>
                <input
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono"
                  value={editConfig.key}
                  onChange={(e) => setEditConfig((c) => ({ ...c, key: e.target.value }))}
                  placeholder="threads"
                />
              </div>
              <div className="flex-1">
                <label className="text-xs text-muted-foreground">Value</label>
                <input
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono"
                  value={editConfig.value}
                  onChange={(e) => setEditConfig((c) => ({ ...c, value: e.target.value }))}
                  placeholder="8"
                />
              </div>
              <Button onClick={handleSetConfig}
                disabled={!editConfig.key} variant="primary">
                <Zap className="w-3 h-3" />设置
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* ── New Download Dialog ── */}
      {showNewDownload && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowNewDownload(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">新建下载</h2>
              <Button onClick={() => setShowNewDownload(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">下载地址 *</label>
                <input
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono"
                  value={newUrl}
                  onChange={(e) => setNewUrl(e.target.value)}
                  placeholder="https://example.com/file.zip"
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">保存路径（可选）</label>
                <input
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono"
                  value={newDest}
                  onChange={(e) => setNewDest(e.target.value)}
                  placeholder="默认: ~/.openmate/download-cache/"
                />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowNewDownload(false)} variant="secondary">取消</Button>
                <Button onClick={handleStartDownload}
                  disabled={downloading || !newUrl} variant="primary">
                  {downloading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Download className="w-3 h-3" />}
                  开始下载
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
