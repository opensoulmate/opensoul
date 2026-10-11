'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, RefreshCw, X, CheckCircle, XCircle, Clock, Upload, Play,
  Filter, ChevronDown, ChevronRight, AlertTriangle, Zap, FileText,
  Shield, Brain, Database, ArrowRight, Trash2,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge, TONES } from '@/components/ui/badge';

// ── Types ───────────────────────────────────────────────────

interface PipelineStep {
  step: string;
  status: string;
  [key: string]: any;
}

interface PipelineRun {
  pipeline_id: string;
  status: string;
  file_id?: string;
  pipeline_type?: string;
  steps: PipelineStep[];
  started_at: number;
  finished_at?: number;
  elapsed_ms?: number;
  error?: string;
  result?: any;
}

interface PipelineHistory {
  pipelines: PipelineRun[];
  total: number;
}

// ── Step icon map ───────────────────────────────────────────

const STEP_ICONS: Record<string, typeof FileText> = {
  vein: Database,
  'sense-ocr': FileText,
  'sense-asr': FileText,
  'sense-text': FileText,
  'sense-video': FileText,
  sense: FileText,
  immune: Shield,
  knowledge: Brain,
};

const STATUS_COLORS: Record<string, string> = {
  completed: TONES.success.text,
  ok: TONES.success.text,
  running: TONES.primary.text,
  blocked: TONES.warning.text,
  partial: TONES.warning.text,
  failed: TONES.destructive.text,
  error: TONES.destructive.text,
  skipped: TONES.muted.text,
};

const STATUS_BG: Record<string, string> = {
  completed: TONES.success.bg,
  ok: TONES.success.bg,
  running: TONES.primary.bg,
  blocked: TONES.warning.bg,
  partial: TONES.warning.bg,
  failed: TONES.destructive.bg,
  error: TONES.destructive.bg,
  skipped: TONES.muted.bg,
};

const PIPELINE_TYPES = [
  { value: 'auto', label: '自动检测' },
  { value: 'ocr', label: 'OCR识别' },
  { value: 'asr', label: '语音转文字' },
  { value: 'text', label: '文本提取' },
  { value: 'video', label: '视频分析' },
];

// ── Helpers ─────────────────────────────────────────────────

function formatElapsed(ms?: number) {
  if (!ms) return '-';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
}

function formatTime(ts: number) {
  if (!ts) return '-';
  return new Date(ts * 1000).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

function StatusBadge({ status }: { status: string }) {
  const label: Record<string, string> = {
    completed: '已完成', ok: '成功', running: '运行中', blocked: '已拦截',
    partial: '部分完成', failed: '失败', error: '错误', skipped: '已跳过',
  };
  return (
    <Badge variant="bare" className={`gap-1 ${STATUS_BG[status] || 'bg-muted'} ${STATUS_COLORS[status] || 'text-muted-foreground'}`}>
      {status === 'completed' || status === 'ok' ? <CheckCircle className="w-2.5 h-2.5" /> :
       status === 'running' ? <Clock className="w-2.5 h-2.5 animate-spin" /> :
       status === 'failed' || status === 'error' ? <XCircle className="w-2.5 h-2.5" /> :
       status === 'blocked' ? <AlertTriangle className="w-2.5 h-2.5" /> : null}
      {label[status] || status}
    </Badge>
  );
}

// ── Step Detail Card ────────────────────────────────────────

function StepCard({ step }: { step: PipelineStep }) {
  const [expanded, setExpanded] = useState(false);
  const Icon = STEP_ICONS[step.step] || Zap;

  return (
    <div className="border border-border rounded-lg bg-card">
      <Button
        onClick={() => setExpanded(!expanded)}
        variant="row" className="gap-3 p-3 hover:bg-muted/30 transition-colors"
      >
        <Icon className={`w-4 h-4 shrink-0 ${STATUS_COLORS[step.status] || 'text-muted-foreground'}`} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">{step.step}</span>
            <StatusBadge status={step.status} />
          </div>
          {step.error && <p className="text-xs text-red-500 mt-0.5 truncate">{step.error}</p>}
          {step.reason && <p className="text-xs text-muted-foreground mt-0.5 truncate">{step.reason}</p>}
        </div>
        {expanded ? <ChevronDown className="w-3.5 h-3.5 shrink-0 text-muted-foreground" /> : <ChevronRight className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />}
      </Button>
      {expanded && (
        <div className="px-3 pb-3 border-t border-border pt-2 space-y-1">
          {Object.entries(step).filter(([k]) => !['step', 'status', 'error', 'reason'].includes(k)).map(([k, v]) => (
            <div key={k} className="flex items-start gap-2 text-xs">
              <span className="text-muted-foreground shrink-0 w-28">{k}</span>
              <span className="font-mono break-all">{typeof v === 'string' ? v : JSON.stringify(v)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────

export default function PipelinePage() {
  const [history, setHistory] = useState<PipelineRun[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [typeFilter, setTypeFilter] = useState('all');
  const [selectedPipeline, setSelectedPipeline] = useState<PipelineRun | null>(null);
  const [showDetail, setShowDetail] = useState(false);

  // Upload state
  const [showUpload, setShowUpload] = useState(false);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadType, setUploadType] = useState('auto');
  const [uploadTags, setUploadTags] = useState('');
  const [uploadSkipImmune, setUploadSkipImmune] = useState(false);
  const [uploadSkipKnowledge, setUploadSkipKnowledge] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<PipelineRun | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const fetchHistory = useCallback(async () => {
    try {
      setLoading(true);
      const data: PipelineHistory = await apiFetch('/api/pipeline/history?limit=100');
      setHistory(Array.isArray(data.pipelines) ? data.pipelines : []);
      setTotal(data.total ?? 0);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchHistory(); }, [fetchHistory]);

  // Auto-refresh while any pipeline is running
  useEffect(() => {
    const hasRunning = history.some((p) => p.status === 'running');
    if (!hasRunning) return;
    const iv = setInterval(fetchHistory, 3000);
    return () => clearInterval(iv);
  }, [history, fetchHistory]);

  const handleUpload = async () => {
    if (!uploadFile) return;
    try {
      setUploading(true);
      setUploadResult(null);
      const form = new FormData();
      form.append('file', uploadFile);
      form.append('pipeline', uploadType);
      form.append('tags', uploadTags);
      form.append('skip_immune', String(uploadSkipImmune));
      form.append('skip_knowledge', String(uploadSkipKnowledge));

      const token = localStorage.getItem('soul_token');
      const res = await fetch('/api/pipeline/upload', {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: form,
      });
      if (!res.ok) throw new Error(`上传失败: ${res.status}`);
      const data: PipelineRun = await res.json();
      setUploadResult(data);
      setShowUpload(false);
      setUploadFile(null);
      setUploadTags('');
      setUploadType('auto');
      setUploadSkipImmune(false);
      setUploadSkipKnowledge(false);
      await fetchHistory();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setUploading(false);
    }
  };

  const filtered = history.filter((p) => {
    if (statusFilter !== 'all' && p.status !== statusFilter) return false;
    if (typeFilter !== 'all' && p.pipeline_type !== typeFilter) return false;
    if (search) {
      const q = search.toLowerCase();
      return (
        p.pipeline_id.toLowerCase().includes(q) ||
        (p.file_id || '').toLowerCase().includes(q) ||
        (p.pipeline_type || '').toLowerCase().includes(q)
      );
    }
    return true;
  });

  // Stats
  const completedCount = history.filter((p) => p.status === 'completed').length;
  const failedCount = history.filter((p) => p.status === 'failed' || p.status === 'blocked').length;
  const runningCount = history.filter((p) => p.status === 'running').length;
  const avgTime = history.filter((p) => p.elapsed_ms).reduce((s, p) => s + (p.elapsed_ms || 0), 0) / (history.filter((p) => p.elapsed_ms).length || 1);

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{total}</div>
          <div className="text-xs text-muted-foreground">总执行次数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-600">{completedCount}</div>
          <div className="text-xs text-muted-foreground">成功完成</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-red-500">{failedCount}</div>
          <div className="text-xs text-muted-foreground">失败/拦截</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{formatElapsed(avgTime)}</div>
          <div className="text-xs text-muted-foreground">平均耗时</div>
        </div>
      </div>

      {/* Upload result banner */}
      {uploadResult && (
        <div className={`rounded-lg border p-3 flex items-start gap-3 ${uploadResult.status === 'completed' ? 'border-emerald-500/30 bg-emerald-500/5' : uploadResult.status === 'blocked' ? 'border-amber-500/30 bg-amber-500/5' : 'border-border bg-card'}`}>
          <div className="flex-1">
            <div className="flex items-center gap-2 text-sm font-medium">
              <StatusBadge status={uploadResult.status} />
              <span>流水线 {uploadResult.pipeline_id}</span>
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              类型: {uploadResult.pipeline_type} | 耗时: {formatElapsed(uploadResult.elapsed_ms)}
            </p>
          </div>
          <Button onClick={() => setUploadResult(null)} variant="icon-sm" className="hover:bg-muted">
            <X className="w-3.5 h-3.5" />
          </Button>
        </div>
      )}

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索流水线ID、文件ID..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="all">全部状态</option>
          <option value="completed">已完成</option>
          <option value="running">运行中</option>
          <option value="blocked">已拦截</option>
          <option value="partial">部分完成</option>
          <option value="failed">失败</option>
        </select>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
        >
          <option value="all">全部类型</option>
          {PIPELINE_TYPES.filter((t) => t.value !== 'auto').map((t) => (
            <option key={t.value} value={t.value}>{t.label}</option>
          ))}
        </select>
        <Button onClick={() => fetchHistory()} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
        <Button onClick={() => setShowUpload(true)} variant="primary" className="gap-1.5">
          <Upload className="w-3.5 h-3.5" />上传文件
        </Button>
      </div>

      {/* Pipeline Timeline */}
      <div className="space-y-2">
        {filtered.map((p) => (
          <div
            key={p.pipeline_id}
            className="rounded-lg border border-border bg-card p-4 hover:bg-muted/30 transition-colors cursor-pointer"
            onClick={() => { setSelectedPipeline(p); setShowDetail(true); }}
          >
            <div className="flex items-center gap-3">
              <StatusBadge status={p.status} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium font-mono truncate">{p.pipeline_id}</span>
                  {p.pipeline_type && (
                    <Badge variant="muted">{p.pipeline_type}</Badge>
                  )}
                </div>
              </div>
              <div className="text-right shrink-0">
                <div className="text-xs text-muted-foreground">{formatTime(p.started_at)}</div>
                <div className="text-xs font-medium">{formatElapsed(p.elapsed_ms)}</div>
              </div>
            </div>
            {/* Step progress bar */}
            <div className="flex items-center gap-1 mt-3">
              {p.steps.map((s, i) => (
                <div key={i} className="flex items-center gap-1">
                  <div
                    className={`h-1.5 rounded-full transition-all ${
                      s.status === 'ok' ? 'bg-green-500' :
                      s.status === 'error' ? 'bg-red-500' :
                      s.status === 'skipped' ? 'bg-muted' :
                      'bg-primary'
                    }`}
                    style={{ width: `${Math.max(30, 100 / p.steps.length)}px` }}
                    title={`${s.step}: ${s.status}`}
                  />
                  {i < p.steps.length - 1 && <ArrowRight className="w-2.5 h-2.5 text-muted-foreground" />}
                </div>
              ))}
            </div>
            {p.error && (
              <p className="text-xs text-red-500 mt-2 truncate">{p.error}</p>
            )}
          </div>
        ))}
      </div>

      {filtered.length === 0 && (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
          <Zap className="w-10 h-10 mb-2 opacity-30" />
          <p className="text-xs">暂无流水线记录</p>
        </div>
      )}

      {/* ── Detail Dialog ── */}
      {showDetail && selectedPipeline && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowDetail(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-2xl mx-4 max-h-[85vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border sticky top-0 bg-card z-10">
              <div className="flex items-center gap-2">
                <Zap className="w-4 h-4" />
                <h2 className="text-base font-medium font-mono">{selectedPipeline.pipeline_id}</h2>
                <StatusBadge status={selectedPipeline.status} />
              </div>
              <Button onClick={() => setShowDetail(false)} variant="icon-sm" className="hover:bg-muted">
                <X className="w-4 h-4" />
              </Button>
            </div>
            <div className="p-4 space-y-4">
              {/* Summary */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div>
                  <div className="text-xs text-muted-foreground">类型</div>
                  <div className="text-sm font-medium">{selectedPipeline.pipeline_type || '-'}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">耗时</div>
                  <div className="text-sm font-medium">{formatElapsed(selectedPipeline.elapsed_ms)}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">开始时间</div>
                  <div className="text-sm">{formatTime(selectedPipeline.started_at)}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">完成时间</div>
                  <div className="text-sm">{formatTime(selectedPipeline.finished_at || 0)}</div>
                </div>
                {selectedPipeline.file_id && (
                  <div className="col-span-2">
                    <div className="text-xs text-muted-foreground">文件ID</div>
                    <div className="text-sm font-mono break-all">{selectedPipeline.file_id}</div>
                  </div>
                )}
              </div>

              {selectedPipeline.error && (
                <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3">
                  <div className="text-xs font-medium text-red-500 mb-1">错误</div>
                  <p className="text-xs text-red-500">{selectedPipeline.error}</p>
                </div>
              )}

              {/* Steps */}
              <div>
                <div className="text-sm font-medium mb-2">处理步骤</div>
                <div className="space-y-2">
                  {selectedPipeline.steps.map((step, i) => (
                    <StepCard key={i} step={step} />
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Upload Dialog ── */}
      {showUpload && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowUpload(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <div className="flex items-center gap-2">
                <Upload className="w-4 h-4" />
                <h2 className="text-base font-medium">上传文件到流水线</h2>
              </div>
              <Button onClick={() => setShowUpload(false)} variant="icon-sm" className="hover:bg-muted">
                <X className="w-4 h-4" />
              </Button>
            </div>
            <div className="p-4 space-y-4">
              {/* File drop */}
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">选择文件</label>
                <div
                  className="border-2 border-dashed border-border rounded-lg p-6 text-center hover:border-primary/50 transition-colors cursor-pointer"
                  onClick={() => fileInputRef.current?.click()}
                  onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); }}
                  onDrop={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    const f = e.dataTransfer.files[0];
                    if (f) setUploadFile(f);
                  }}
                >
                  {uploadFile ? (
                    <div className="flex items-center justify-center gap-2">
                      <FileText className="w-5 h-5 text-primary" />
                      <div>
                        <p className="text-sm font-medium">{uploadFile.name}</p>
                        <p className="text-xs text-muted-foreground">{(uploadFile.size / 1024).toFixed(1)} KB</p>
                      </div>
                      <Button
                        onClick={(e) => { e.stopPropagation(); setUploadFile(null); }}
                        variant="icon-sm" className="hover:bg-muted ml-2"
                      >
                        <X className="w-3.5 h-3.5" />
                      </Button>
                    </div>
                  ) : (
                    <>
                      <Upload className="w-8 h-8 mx-auto mb-2 text-muted-foreground" />
                      <p className="text-xs text-muted-foreground">拖拽文件到此处，或点击选择</p>
                      <p className="text-[10px] text-muted-foreground mt-1">支持 PDF、图片、音频、视频、文本等</p>
                    </>
                  )}
                </div>
                <input
                  ref={fileInputRef}
                  type="file"
                  className="hidden"
                  onChange={(e) => { const f = e.target.files?.[0]; if (f) setUploadFile(f); }}
                />
              </div>

              {/* Pipeline type */}
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">流水线类型</label>
                <select
                  className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
                  value={uploadType}
                  onChange={(e) => setUploadType(e.target.value)}
                >
                  {PIPELINE_TYPES.map((t) => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </select>
              </div>

              {/* Tags */}
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">标签（逗号分隔）</label>
                <input
                  className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                  placeholder="例如: 报告, 季度, 2026"
                  value={uploadTags}
                  onChange={(e) => setUploadTags(e.target.value)}
                />
              </div>

              {/* Skip options */}
              <div className="flex items-center gap-4">
                <label className="flex items-center gap-2 text-xs cursor-pointer">
                  <input
                    type="checkbox"
                    checked={uploadSkipImmune}
                    onChange={(e) => setUploadSkipImmune(e.target.checked)}
                    className="rounded border-border"
                  />
                  跳过安全扫描
                </label>
                <label className="flex items-center gap-2 text-xs cursor-pointer">
                  <input
                    type="checkbox"
                    checked={uploadSkipKnowledge}
                    onChange={(e) => setUploadSkipKnowledge(e.target.checked)}
                    className="rounded border-border"
                  />
                  跳过知识入库
                </label>
              </div>

              {/* Actions */}
              <div className="flex items-center gap-2 pt-2">
                <Button onClick={handleUpload}
                  disabled={!uploadFile || uploading} variant="primary" className="gap-1.5">
                  {uploading ? <Clock className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
                  {uploading ? '处理中...' : '开始处理'}
                </Button>
                <Button onClick={() => setShowUpload(false)} variant="secondary" className="px-4 py-2">
                  取消
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Error toast */}
      {error && (
        <div className="fixed bottom-4 right-4 z-50 flex items-center gap-2 px-3 py-2 bg-red-500/10 border border-red-500/30 rounded-lg text-xs text-red-500">
          <AlertTriangle className="w-3.5 h-3.5" />
          {error}
          <Button onClick={() => setError('')} variant="icon-xs" className="hover:bg-red-500/20">
            <X className="w-3 h-3" />
          </Button>
        </div>
      )}
    </div>
  );
}
