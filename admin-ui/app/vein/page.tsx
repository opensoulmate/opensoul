'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  RefreshCw, X, Droplets, Upload, Download, Trash2, Database,
  HardDrive, File, Search, Plus, Loader2, AlertCircle, Clock,
  Tag, Eye, RotateCcw, Zap, Package, ChevronDown, ChevronRight,
} from 'lucide-react';
import { Tab, TabBar } from '@/components/ui/tab';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

// ── Types ───────────────────────────────────────────────────

interface VeinFile {
  file_id: string;
  name: string;
  size: number;
  mime_type: string;
  content_hash: string;
  tags: string[];
  created_at: string;
  updated_at: string;
  version_count: number;
}

interface CacheStats {
  total_keys: number;
  total_size_bytes: number;
  hit_count: number;
  miss_count: number;
  hit_rate: number;
}

interface ChunkedUpload {
  upload_id: string;
  filename: string;
  total_size: number;
  chunk_size: number;
  total_chunks: number;
  uploaded_chunks: number;
  status: string;
  tags: string[];
}

interface VeinStats {
  status: string;
  component: string;
  store: { total_files: number; total_size_bytes: number; total_versions: number };
  cache: CacheStats;
  uploads: { active: number; total: number };
}

// ── Helpers ─────────────────────────────────────────────────

function formatBytes(bytes: number) {
  if (!bytes) return '0 B';
  if (bytes >= 1073741824) return `${(bytes / 1073741824).toFixed(2)} GB`;
  if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

function formatTime(ts: string) {
  if (!ts) return '-';
  try {
    return new Date(ts).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    });
  } catch { return ts; }
}

function mimeIcon(mime: string) {
  if (mime.startsWith('image/')) return '🖼️';
  if (mime.startsWith('video/')) return '🎬';
  if (mime.startsWith('audio/')) return '🎵';
  if (mime.includes('pdf')) return '📄';
  if (mime.includes('json')) return '📋';
  if (mime.includes('zip') || mime.includes('tar')) return '📦';
  return '📁';
}

// ── Main Page ───────────────────────────────────────────────

export default function VeinPage() {
  const [stats, setStats] = useState<VeinStats | null>(null);
  const [files, setFiles] = useState<VeinFile[]>([]);
  const [uploads, setUploads] = useState<ChunkedUpload[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<'files' | 'cache' | 'uploads'>('files');

  // File filters
  const [search, setSearch] = useState('');
  const [tagFilter, setTagFilter] = useState('');
  const [expandedFile, setExpandedFile] = useState<string | null>(null);

  // Cache
  const [cacheKey, setCacheKey] = useState('');
  const [cacheValue, setCacheValue] = useState('');

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError('');
    const results = await Promise.allSettled([
      apiFetch('/api/vein/stats'),
      apiFetch('/api/vein/files?limit=100'),
      apiFetch('/api/vein/upload/chunked'),
    ]);
    const [sRes, fRes, uRes] = results;
    if (sRes.status === 'fulfilled') setStats(sRes.value);
    if (fRes.status === 'fulfilled') setFiles(fRes.value.files || []);
    if (uRes.status === 'fulfilled') setUploads(uRes.value.uploads || []);
    if (results.every(r => r.status === 'rejected')) setError('无法连接到 OpenVein 服务');
    setLoading(false);
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  // ── File Actions ──────────────────────────────────────────

  const handleDeleteFile = async (fileId: string) => {
    if (!confirm('确定要删除该文件？')) return;
    try {
      await apiFetch(`/api/vein/files/${fileId}`, { method: 'DELETE' });
      fetchData();
    } catch (e: any) { setError(e.message); }
  };

  const handleRollback = async (fileId: string, version: number) => {
    if (!confirm(`确定要回滚到版本 ${version}？`)) return;
    try {
      await apiFetch(`/api/vein/files/${fileId}/rollback/${version}`, { method: 'POST' });
      fetchData();
    } catch (e: any) { setError(e.message); }
  };

  // ── Cache Actions ─────────────────────────────────────────

  const handleCacheClear = async () => {
    try {
      await apiFetch('/api/vein/cache/clear', { method: 'POST' });
      fetchData();
    } catch (e: any) { setError(e.message); }
  };

  const handleCacheCleanup = async () => {
    try {
      await apiFetch('/api/vein/cache/cleanup', { method: 'POST' });
      fetchData();
    } catch (e: any) { setError(e.message); }
  };

  // ── Filters ───────────────────────────────────────────────

  const filtered = files.filter(f => {
    if (search && !f.name.toLowerCase().includes(search.toLowerCase()) && !f.file_id.includes(search)) return false;
    if (tagFilter && !f.tags?.some(t => t.includes(tagFilter))) return false;
    return true;
  });

  const allTags = [...new Set(files.flatMap(f => f.tags || []))].sort();

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* ── Stats ── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <File className="w-3.5 h-3.5 text-blue-500" />
            <span className="text-xs text-muted-foreground">文件</span>
          </div>
          <div className="text-2xl font-bold">{stats?.store?.total_files || files.length}</div>
          <div className="text-[10px] text-muted-foreground">{formatBytes(stats?.store?.total_size_bytes || 0)}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <Database className="w-3.5 h-3.5 text-purple-500" />
            <span className="text-xs text-muted-foreground">版本</span>
          </div>
          <div className="text-2xl font-bold">{stats?.store?.total_versions || 0}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <HardDrive className="w-3.5 h-3.5 text-amber-500" />
            <span className="text-xs text-muted-foreground">缓存</span>
          </div>
          <div className="text-2xl font-bold">{stats?.cache?.total_keys || 0}</div>
          <div className="text-[10px] text-muted-foreground">命中率 {((stats?.cache?.hit_rate || 0) * 100).toFixed(0)}%</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <Package className="w-3.5 h-3.5 text-green-500" />
            <span className="text-xs text-muted-foreground">分片上传</span>
          </div>
          <div className="text-2xl font-bold">{stats?.uploads?.active || 0}</div>
          <div className="text-[10px] text-muted-foreground">{stats?.uploads?.total || 0} 总计</div>
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
          <Tab active={tab === 'files'} onClick={() => setTab('files')}>文件</Tab>
          <Tab active={tab === 'cache'} onClick={() => setTab('cache')}>缓存</Tab>
          <Tab active={tab === 'uploads'} onClick={() => setTab('uploads')}>上传</Tab>
        </TabBar>
        {tab === 'files' && (
          <>
            <div className="relative flex-1 min-w-[150px]">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
              <input className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md" placeholder="搜索文件名..." value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>
            {allTags.length > 0 && (
              <select className="px-2 py-1.5 text-xs bg-muted border border-border rounded-md" value={tagFilter} onChange={(e) => setTagFilter(e.target.value)}>
                <option value="">全部标签</option>
                {allTags.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            )}
          </>
        )}
        <Button onClick={fetchData} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
      </div>

      {/* ── Files Tab ── */}
      {tab === 'files' && (
        <div className="space-y-1.5">
          {filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <File className="w-10 h-10 mb-2 opacity-30" />
              <p className="text-xs">{search ? '没有匹配的文件' : '暂无文件'}</p>
            </div>
          ) : (
            filtered.map((file) => (
              <div key={file.file_id} className="rounded-lg border border-border bg-card hover:bg-muted/20 transition-colors">
                <div className="flex items-center gap-3 p-2.5 cursor-pointer" onClick={() => setExpandedFile(expandedFile === file.file_id ? null : file.file_id)}>
                  <span className="text-lg">{mimeIcon(file.mime_type)}</span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-medium truncate">{file.name}</span>
                      <Badge variant="muted">{file.mime_type}</Badge>
                      {file.version_count > 1 && (
                        <Badge variant="info">v{file.version_count}</Badge>
                      )}
                    </div>
                    <div className="flex items-center gap-3 mt-0.5 text-[10px] text-muted-foreground">
                      <span>{formatBytes(file.size)}</span>
                      <span className="font-mono">{file.file_id.slice(0, 12)}</span>
                      <span>{formatTime(file.created_at)}</span>
                    </div>
                  </div>
                  {file.tags?.length > 0 && (
                    <div className="flex gap-1 shrink-0">
                      {file.tags.slice(0, 3).map(t => (
                        <Badge key={t} variant="default">{t}</Badge>
                      ))}
                    </div>
                  )}
                  <div className="flex items-center gap-1 shrink-0">
                    <Button onClick={(e) => { e.stopPropagation(); handleDeleteFile(file.file_id); }} variant="icon-sm" className="hover:bg-red-500/10" title="删除">
                      <Trash2 className="w-3.5 h-3.5 text-red-500" />
                    </Button>
                  </div>
                  {expandedFile === file.file_id ? <ChevronDown className="w-3 h-3 text-muted-foreground shrink-0" /> : <ChevronRight className="w-3 h-3 text-muted-foreground shrink-0" />}
                </div>
                {expandedFile === file.file_id && (
                  <div className="px-2.5 pb-2.5 border-t border-border pt-2 ml-10">
                    <div className="grid grid-cols-2 gap-2 text-[10px] text-muted-foreground">
                      <div>Hash: <span className="font-mono">{file.content_hash?.slice(0, 16)}...</span></div>
                      <div>更新: {formatTime(file.updated_at)}</div>
                    </div>
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {/* ── Cache Tab ── */}
      {tab === 'cache' && (
        <div className="space-y-3">
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-center gap-2 mb-3">
              <HardDrive className="w-4 h-4 text-amber-500" />
              <span className="text-sm font-medium">缓存管理</span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
              <div className="text-center">
                <div className="text-lg font-bold">{stats?.cache?.total_keys || 0}</div>
                <div className="text-[10px] text-muted-foreground">键数</div>
              </div>
              <div className="text-center">
                <div className="text-lg font-bold">{formatBytes(stats?.cache?.total_size_bytes || 0)}</div>
                <div className="text-[10px] text-muted-foreground">大小</div>
              </div>
              <div className="text-center">
                <div className="text-lg font-bold text-green-500">{stats?.cache?.hit_count || 0}</div>
                <div className="text-[10px] text-muted-foreground">命中</div>
              </div>
              <div className="text-center">
                <div className="text-lg font-bold text-red-400">{stats?.cache?.miss_count || 0}</div>
                <div className="text-[10px] text-muted-foreground">未命中</div>
              </div>
            </div>
            <div className="flex gap-2">
              <Button onClick={handleCacheCleanup} variant="chip-sm" className="flex items-center gap-1 bg-amber-500/10 text-amber-600 border border-amber-500/20 hover:bg-amber-500/20">
                <Zap className="w-3 h-3" />清理过期
              </Button>
              <Button onClick={handleCacheClear} variant="chip-sm" className="flex items-center gap-1 bg-red-500/10 text-red-500 border border-red-500/20 hover:bg-red-500/20">
                <Trash2 className="w-3 h-3" />清空缓存
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* ── Uploads Tab ── */}
      {tab === 'uploads' && (
        <div className="space-y-2">
          {uploads.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <Upload className="w-10 h-10 mb-2 opacity-30" />
              <p className="text-xs">暂无分片上传会话</p>
            </div>
          ) : (
            uploads.map((u) => (
              <div key={u.upload_id} className="rounded-lg border border-border bg-card p-3 hover:bg-muted/30 transition-colors">
                <div className="flex items-center gap-3">
                  <Upload className="w-4 h-4 text-blue-500 shrink-0" />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-medium">{u.filename}</span>
                      <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${u.status === 'completed' ? 'bg-emerald-500/10 text-emerald-500' : u.status === 'uploading' ? 'bg-blue-500/10 text-blue-500' : 'bg-muted text-muted-foreground'}`}>
                        {u.status}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 mt-0.5 text-[10px] text-muted-foreground">
                      <span>{formatBytes(u.total_size)}</span>
                      <span>{u.uploaded_chunks}/{u.total_chunks} 分片</span>
                      <span>分片大小: {formatBytes(u.chunk_size)}</span>
                    </div>
                    <div className="w-full bg-muted rounded-full h-1.5 mt-1">
                      <div className="bg-primary h-1.5 rounded-full transition-all" style={{ width: `${(u.uploaded_chunks / u.total_chunks) * 100}%` }} />
                    </div>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* ── Footer ── */}
      <div className="text-[10px] text-muted-foreground text-center">
        OpenVein · 血管系统 · 文件存储 / 版本管理 / 缓存 / 分片上传
      </div>
    </div>
  );
}
