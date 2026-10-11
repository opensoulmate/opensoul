'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  RefreshCw, X, GitBranch, GitCommit, AlertTriangle,
  CheckCircle, Loader2, ArrowUp, ArrowDown, FileText,
  Plus, Minus, Edit3, Shield, Activity, Clock, Terminal,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

// ── Types ───────────────────────────────────────────────────

interface GitStatus {
  branch: string;
  modified: number;
  staged: number;
  untracked: number;
  ahead: number;
  behind: number;
}

// ── Helpers ─────────────────────────────────────────────────

function formatTime() {
  return new Date().toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

// ── Main Page ───────────────────────────────────────────────

export default function GitPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [status, setStatus] = useState<GitStatus | null>(null);
  const [cwd, setCwd] = useState('');
  const [customCwd, setCustomCwd] = useState('');

  // Commit
  const [commitMsg, setCommitMsg] = useState('');
  const [committing, setCommitting] = useState(false);
  const [commitResult, setCommitResult] = useState<{ success: boolean; message: string } | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      setLoading(true);
      setError('');
      const params = customCwd ? `?cwd=${encodeURIComponent(customCwd)}` : '';
      const data = await apiFetch(`/api/git/status${params}`);
      setStatus(data);
      setCwd(customCwd || '~');
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [customCwd]);

  useEffect(() => { fetchStatus(); }, []);

  const handleCommit = async () => {
    if (!commitMsg.trim()) return;
    setCommitting(true);
    setCommitResult(null);
    try {
      const data = await apiFetch('/api/git/commit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: commitMsg,
          add_all: true,
          cwd: customCwd || undefined,
        }),
      });
      setCommitResult(data);
      if (data.success) {
        setCommitMsg('');
        fetchStatus();
      }
    } catch (e: any) {
      setCommitResult({ success: false, message: e.message });
    } finally {
      setCommitting(false);
    }
  };

  const hasChanges = status ? (status.modified + status.staged + status.untracked) > 0 : false;
  const isSynced = status ? status.ahead === 0 && status.behind === 0 : true;

  return (
    <div className="space-y-4">
      {/* ── Stats ── */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <GitBranch className="w-4 h-4 text-primary" />
            <span className="text-lg font-bold truncate">{status?.branch || '-'}</span>
          </div>
          <div className="text-xs text-muted-foreground">当前分支</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-amber-500">{status?.modified || 0}</div>
          <div className="text-xs text-muted-foreground">已修改</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-500">{status?.staged || 0}</div>
          <div className="text-xs text-muted-foreground">已暂存</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-500">{status?.untracked || 0}</div>
          <div className="text-xs text-muted-foreground">未跟踪</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            {status && status.ahead > 0 && (
              <span className="flex items-center gap-0.5 text-amber-500">
                <ArrowUp className="w-3 h-3" />{status.ahead}
              </span>
            )}
            {status && status.behind > 0 && (
              <span className="flex items-center gap-0.5 text-red-500">
                <ArrowDown className="w-3 h-3" />{status.behind}
              </span>
            )}
            {isSynced && (
              <span className="text-green-500 text-sm">已同步</span>
            )}
          </div>
          <div className="text-xs text-muted-foreground">远程状态</div>
        </div>
      </div>

      {/* ── Info Banner ── */}
      <div className="rounded-lg border border-blue-500/30 bg-blue-500/5 p-3 flex items-center gap-2">
        <GitBranch className="w-4 h-4 text-blue-500 shrink-0" />
        <div>
          <span className="text-xs font-medium text-blue-500">GitAPI 版本控制</span>
          <p className="text-[10px] text-muted-foreground mt-0.5">
            查看仓库状态、分支信息，快速提交更改。支持指定工作目录。
          </p>
        </div>
      </div>

      {/* ── Toolbar ── */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Terminal className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="工作目录（留空使用 ~）"
            value={customCwd}
            onChange={(e) => setCustomCwd(e.target.value)}
          />
        </div>
        <Button onClick={fetchStatus}
          disabled={loading} variant="secondary">
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
          刷新
        </Button>
      </div>

      {/* ── Status Detail ── */}
      <div className="rounded-lg border border-border bg-card">
        <div className="flex items-center gap-2 p-4 border-b border-border">
          <Activity className="w-4 h-4" />
          <span className="text-sm font-medium">仓库状态</span>
          <span className="text-[10px] text-muted-foreground ml-auto font-mono">{cwd}</span>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
          </div>
        ) : status ? (
          <div className="p-4 space-y-3">
            {/* Branch info */}
            <div className="flex items-center gap-3 p-3 rounded-lg bg-muted/30">
              <GitBranch className="w-5 h-5 text-primary" />
              <div>
                <div className="text-sm font-medium">{status.branch}</div>
                <div className="text-[10px] text-muted-foreground">当前分支</div>
              </div>
              {status.ahead > 0 && (
                <Badge variant="warning" className="ml-auto gap-1">
                  <ArrowUp className="w-3 h-3" />领先 {status.ahead} 提交
                </Badge>
              )}
              {status.behind > 0 && (
                <Badge variant="destructive" className="ml-auto gap-1">
                  <ArrowDown className="w-3 h-3" />落后 {status.behind} 提交
                </Badge>
              )}
              {isSynced && (
                <Badge variant="success" className="ml-auto gap-1">
                  <CheckCircle className="w-3 h-3" />已同步
                </Badge>
              )}
            </div>

            {/* Change counts */}
            <div className="grid grid-cols-3 gap-3">
              <div className={`rounded-lg border p-3 text-center ${status.staged > 0 ? 'border-emerald-500/30 bg-green-500/5' : 'border-border bg-muted/30'}`}>
                <div className={`text-xl font-bold ${status.staged > 0 ? 'text-green-500' : 'text-muted-foreground'}`}>{status.staged}</div>
                <div className="text-[10px] text-muted-foreground">已暂存</div>
              </div>
              <div className={`rounded-lg border p-3 text-center ${status.modified > 0 ? 'border-amber-500/30 bg-amber-500/5' : 'border-border bg-muted/30'}`}>
                <div className={`text-xl font-bold ${status.modified > 0 ? 'text-amber-500' : 'text-muted-foreground'}`}>{status.modified}</div>
                <div className="text-[10px] text-muted-foreground">已修改</div>
              </div>
              <div className={`rounded-lg border p-3 text-center ${status.untracked > 0 ? 'border-blue-500/30 bg-blue-500/5' : 'border-border bg-muted/30'}`}>
                <div className={`text-xl font-bold ${status.untracked > 0 ? 'text-blue-500' : 'text-muted-foreground'}`}>{status.untracked}</div>
                <div className="text-[10px] text-muted-foreground">未跟踪</div>
              </div>
            </div>

            {/* Clean state */}
            {!hasChanges && (
              <div className="flex items-center gap-2 p-3 rounded-lg bg-green-500/5 border border-green-500/20">
                <CheckCircle className="w-4 h-4 text-green-500" />
                <span className="text-xs text-green-500">工作区干净，无待提交更改</span>
              </div>
            )}
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
            <GitBranch className="w-8 h-8 mb-2 opacity-30" />
            <p className="text-xs">无法获取Git状态</p>
          </div>
        )}
      </div>

      {/* ── Quick Commit ── */}
      <div className="rounded-lg border border-border bg-card">
        <div className="flex items-center gap-2 p-4 border-b border-border">
          <GitCommit className="w-4 h-4" />
          <span className="text-sm font-medium">快速提交</span>
          <span className="text-[10px] text-muted-foreground ml-auto">自动暂存所有更改并提交</span>
        </div>
        <div className="p-4 space-y-3">
          <div className="flex gap-2">
            <input
              className="flex-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
              placeholder="提交信息..."
              value={commitMsg}
              onChange={(e) => setCommitMsg(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleCommit(); }}
            />
            <Button onClick={handleCommit}
              disabled={committing || !commitMsg.trim()} variant="primary" className="gap-1.5 px-4 py-1.5">
              {committing ? <Loader2 className="w-3 h-3 animate-spin" /> : <GitCommit className="w-3 h-3" />}
              提交
            </Button>
          </div>

          {commitResult && (
            <div className={`p-3 rounded-lg text-xs ${commitResult.success ? 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/30' : 'bg-red-500/10 text-red-500 border border-red-500/30'}`}>
              {commitResult.success ? (
                <div className="flex items-center gap-2">
                  <CheckCircle className="w-4 h-4" />
                  <span>提交成功</span>
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  <AlertTriangle className="w-4 h-4" />
                  <span>{commitResult.message}</span>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

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
