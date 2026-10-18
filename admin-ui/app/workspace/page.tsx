'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, RefreshCw, X, FolderOpen, File, FileText, FileCode,
  FileImage, ChevronRight, ChevronDown, ArrowLeft, Home,
  Terminal, Play, Loader2, AlertTriangle, CheckCircle,
  HardDrive, Clock, Eye, Edit3, Save, Download, Trash2,
  Folder, Database, Shield, Settings,
} from 'lucide-react';
import { Button } from '../../components/ui/button';

// ── Types ───────────────────────────────────────────────────

interface DirEntry {
  name: string;
  path: string;
  type: 'directory' | 'file' | 'unknown';
  size: number | null;
  modified: number | null;
  hidden: boolean;
}

interface FileContent {
  path: string;
  content: string;
  size: number;
}

interface CommandResult {
  output: string;
  exit_code: number;
  cmd: string;
}

// ── Helpers ─────────────────────────────────────────────────

function formatSize(bytes: number | null) {
  if (bytes === null || bytes === undefined) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function formatTime(ts: number | null) {
  if (!ts) return '-';
  try {
    return new Date(ts * 1000).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    });
  } catch { return '-'; }
}

function getFileIcon(name: string, isDir: boolean) {
  if (isDir) return <Folder className="w-4 h-4 text-amber-500" />;
  const ext = name.split('.').pop()?.toLowerCase() || '';
  if (['py', 'js', 'ts', 'tsx', 'jsx', 'rs', 'go', 'java', 'c', 'cpp', 'h'].includes(ext))
    return <FileCode className="w-4 h-4 text-blue-500" />;
  if (['md', 'txt', 'log', 'json', 'yaml', 'yml', 'toml', 'ini', 'cfg'].includes(ext))
    return <FileText className="w-4 h-4 text-emerald-500" />;
  if (['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'ico'].includes(ext))
    return <FileImage className="w-4 h-4 text-violet-500" />;
  return <File className="w-4 h-4 text-muted-foreground" />;
}

function Breadcrumb({ path, onNavigate }: { path: string; onNavigate: (p: string) => void }) {
  const parts = path.split('/').filter(Boolean);
  return (
    <div className="flex items-center gap-1 text-xs overflow-x-auto">
      <Button
        onClick={() => onNavigate('~')}
        variant="icon-sm" className="shrink-0 hover:bg-muted"
        title="Home"
      >
        <Home className="w-3.5 h-3.5" />
      </Button>
      {parts.map((part, i) => {
        const subPath = '/' + parts.slice(0, i + 1).join('/');
        return (
          <span key={i} className="flex items-center gap-1 shrink-0">
            <ChevronRight className="w-3 h-3 text-muted-foreground" />
            <Button variant="bare"
              onClick={() => onNavigate(subPath)}
              className={`hover:text-primary hover:underline ${i === parts.length - 1 ? 'text-foreground font-medium' : 'text-muted-foreground'}`}
            >
              {part}
            </Button>
          </span>
        );
      })}
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────

export default function WorkspacePage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [currentPath, setCurrentPath] = useState('~');
  const [entries, setEntries] = useState<DirEntry[]>([]);
  const [showHidden, setShowHidden] = useState(false);
  const [search, setSearch] = useState('');

  // File viewer
  const [viewingFile, setViewingFile] = useState<FileContent | null>(null);
  const [loadingFile, setLoadingFile] = useState(false);

  // Terminal
  const [showTerminal, setShowTerminal] = useState(false);
  const [cmdInput, setCmdInput] = useState('');
  const [cmdHistory, setCmdHistory] = useState<CommandResult[]>([]);
  const [cmdRunning, setCmdRunning] = useState(false);
  const terminalRef = useRef<HTMLDivElement>(null);

  const fetchDir = useCallback(async (path: string) => {
    try {
      setLoading(true);
      setError('');
      const data = await apiFetch(`/api/dir?path=${encodeURIComponent(path)}`);
      setEntries(data.entries || []);
      setCurrentPath(data.path || path);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchDir(currentPath); }, []);

  const navigateTo = useCallback((path: string) => {
    setViewingFile(null);
    setSearch('');
    fetchDir(path);
  }, [fetchDir]);

  const handleEntryClick = useCallback((entry: DirEntry) => {
    if (entry.type === 'directory') {
      navigateTo(entry.path);
    } else {
      // Load file
      setLoadingFile(true);
      apiFetch(`/api/file?path=${encodeURIComponent(entry.path)}`)
        .then((data) => setViewingFile(data))
        .catch((e) => setError(e.message))
        .finally(() => setLoadingFile(false));
    }
  }, [navigateTo]);

  const handleExecute = useCallback(async () => {
    if (!cmdInput.trim()) return;
    setCmdRunning(true);
    try {
      const data = await apiFetch('/api/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cmd: cmdInput, cwd: currentPath === '~' ? undefined : currentPath }),
      });
      setCmdHistory((prev) => [...prev, data]);
      setCmdInput('');
    } catch (e: any) {
      setCmdHistory((prev) => [...prev, { output: `Error: ${e.message}`, exit_code: 1, cmd: cmdInput }]);
    } finally {
      setCmdRunning(false);
    }
  }, [cmdInput, currentPath]);

  // Auto-scroll terminal
  useEffect(() => {
    if (terminalRef.current) {
      terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
    }
  }, [cmdHistory]);

  // Filter entries
  const filtered = entries.filter((e) => {
    if (!showHidden && e.hidden) return false;
    if (search) {
      return e.name.toLowerCase().includes(search.toLowerCase());
    }
    return true;
  });

  const dirCount = entries.filter((e) => e.type === 'directory').length;
  const fileCount = entries.filter((e) => e.type === 'file').length;
  const totalSize = entries.reduce((s, e) => s + (e.size || 0), 0);

  return (
    <div className="space-y-4">
      {/* ── Stats ── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{dirCount}</div>
          <div className="text-xs text-muted-foreground">目录</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-500">{fileCount}</div>
          <div className="text-xs text-muted-foreground">文件</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{formatSize(totalSize)}</div>
          <div className="text-xs text-muted-foreground">总大小</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-emerald-500">{entries.filter((e) => !e.hidden).length}</div>
          <div className="text-xs text-muted-foreground">可见项</div>
        </div>
      </div>

      {/* ── Info Banner ── */}
      <div className="rounded-lg border border-blue-500/30 bg-blue-500/5 p-3 flex items-center gap-2">
        <HardDrive className="w-4 h-4 text-blue-500 shrink-0" />
        <div>
          <span className="text-xs font-medium text-blue-500">OpenWorkspace 工作空间</span>
          <p className="text-[10px] text-muted-foreground mt-0.5">
            服务端文件浏览器和命令终端。仅限 Home 和 /tmp 目录。支持查看、编辑文件和执行命令。
          </p>
        </div>
      </div>

      {/* ── Breadcrumb + Toolbar ── */}
      <div className="rounded-lg border border-border bg-card">
        <div className="flex items-center gap-2 p-3 border-b border-border">
          <Button
            onClick={() => {
              const parent = currentPath.split('/').slice(0, -1).join('/') || '~';
              navigateTo(parent);
            }}
            variant="icon-md" className="hover:bg-muted shrink-0"
            title="上级目录"
          >
            <ArrowLeft className="w-4 h-4" />
          </Button>
          <div className="flex-1 min-w-0">
            <Breadcrumb path={currentPath} onNavigate={navigateTo} />
          </div>
          <div className="relative w-48">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
            <input
              className="w-full pl-7 pr-2 py-1 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
              placeholder="筛选..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <label className="flex items-center gap-1 text-xs text-muted-foreground cursor-pointer shrink-0">
            <input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} className="rounded" />
            隐藏文件
          </label>
          <Button
            onClick={() => fetchDir(currentPath)}
            variant="icon-md" className="hover:bg-muted shrink-0"
            title="刷新"
          >
            <RefreshCw className="w-4 h-4" />
          </Button>
          <Button
            onClick={() => setShowTerminal(!showTerminal)}
            variant="icon-md" className={`shrink-0  ${showTerminal ? 'bg-primary/10 text-primary' : 'hover:bg-muted'}`}
            title="终端"
          >
            <Terminal className="w-4 h-4" />
          </Button>
        </div>

        {/* ── File List ── */}
        <div className="max-h-[500px] overflow-auto">
          {loading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
              <FolderOpen className="w-8 h-8 mb-2 opacity-30" />
              <p className="text-xs">{search ? '无匹配项' : '空目录'}</p>
            </div>
          ) : (
            filtered.map((entry) => (
              <Button
                key={entry.path}
                onClick={() => handleEntryClick(entry)}
                variant="row" className="gap-3 px-3 py-2 hover:bg-muted/30 transition-colors border-b border-border/50 last:border-0"
              >
                {getFileIcon(entry.name, entry.type === 'directory')}
                <div className="flex-1 min-w-0">
                  <span className={`text-xs ${entry.type === 'directory' ? 'font-medium' : ''} truncate block`}>
                    {entry.name}
                    {entry.type === 'directory' && '/'}
                  </span>
                </div>
                <span className="text-[10px] text-muted-foreground w-16 text-right shrink-0">
                  {entry.type === 'file' ? formatSize(entry.size) : '-'}
                </span>
                <span className="text-[10px] text-muted-foreground w-24 text-right shrink-0">
                  {formatTime(entry.modified)}
                </span>
              </Button>
            ))
          )}
        </div>
      </div>

      {/* ── File Viewer ── */}
      {viewingFile && (
        <div className="rounded-lg border border-border bg-card">
          <div className="flex items-center gap-2 p-3 border-b border-border">
            <FileText className="w-4 h-4 text-emerald-500" />
            <span className="text-sm font-medium truncate flex-1">{viewingFile.path}</span>
            <span className="text-[10px] text-muted-foreground">{formatSize(viewingFile.size)}</span>
            <Button
              onClick={() => setViewingFile(null)}
              variant="icon-sm" className="hover:bg-muted"
            >
              <X className="w-3.5 h-3.5" />
            </Button>
          </div>
          <pre className="p-3 text-xs font-mono overflow-auto max-h-[400px] text-muted-foreground whitespace-pre-wrap break-all">
            {viewingFile.content}
          </pre>
        </div>
      )}

      {loadingFile && (
        <div className="rounded-lg border border-border bg-card p-4 flex items-center gap-2">
          <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
          <span className="text-xs text-muted-foreground">加载文件...</span>
        </div>
      )}

      {/* ── Terminal ── */}
      {showTerminal && (
        <div className="rounded-lg border border-border bg-card">
          <div className="flex items-center gap-2 p-3 border-b border-border">
            <Terminal className="w-4 h-4 text-emerald-500" />
            <span className="text-sm font-medium">终端</span>
            <span className="text-[10px] text-muted-foreground font-mono">{currentPath}</span>
            <Button
              onClick={() => { setCmdHistory([]); }}
              variant="icon-sm" className="ml-auto hover:bg-muted text-[10px] text-muted-foreground"
            >
              清空
            </Button>
            <Button
              onClick={() => setShowTerminal(false)}
              variant="icon-sm" className="hover:bg-muted"
            >
              <X className="w-3.5 h-3.5" />
            </Button>
          </div>

          {/* Output */}
          <div ref={terminalRef} className="p-3 max-h-[300px] overflow-auto font-mono text-xs">
            {cmdHistory.length === 0 && (
              <p className="text-muted-foreground">输入命令执行...</p>
            )}
            {cmdHistory.map((r, i) => (
              <div key={i} className="mb-2">
                <div className="flex items-center gap-1 text-primary">
                  <span>$</span>
                  <span>{r.cmd}</span>
                  {r.exit_code !== 0 && (
                    <span className="text-red-500 text-[10px] ml-2">exit {r.exit_code}</span>
                  )}
                </div>
                {r.output && (
                  <pre className="text-muted-foreground whitespace-pre-wrap mt-0.5 break-all">{r.output}</pre>
                )}
              </div>
            ))}
          </div>

          {/* Input */}
          <div className="flex items-center gap-2 p-3 border-t border-border">
            <span className="text-primary font-mono text-xs">$</span>
            <input
              className="flex-1 px-2 py-1 text-xs font-mono bg-transparent focus:outline-none"
              placeholder="输入命令..."
              value={cmdInput}
              onChange={(e) => setCmdInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleExecute(); }}
              disabled={cmdRunning}
            />
            <Button
              onClick={handleExecute}
              disabled={cmdRunning || !cmdInput.trim()}
              variant="icon-md" className="hover:bg-muted disabled:opacity-30"
            >
              {cmdRunning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            </Button>
          </div>
        </div>
      )}

      {/* ── Error ── */}
      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-red-500 shrink-0" />
          <span className="text-xs text-red-500">{error}</span>
          <Button onClick={() => setError('')} variant="icon-sm" className="ml-auto hover:bg-muted">
            <X className="w-3 h-3" />
          </Button>
        </div>
      )}
    </div>
  );
}
