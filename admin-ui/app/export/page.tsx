'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Download, FileJson, FileText, FileSpreadsheet, File,
  RefreshCw, X, AlertCircle, CheckCircle, Loader2, Database,
  Tag, Layers, Activity,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

// ── Types ───────────────────────────────────────────────────

interface ExportFormat {
  id: string;
  label: string;
  description: string;
  icon: typeof FileJson;
  endpoint: string;
  mimeType: string;
  ext: string;
}

const FORMATS: ExportFormat[] = [
  {
    id: 'json',
    label: 'JSON',
    description: '完整数据导出，包含知识、实体和标签的所有字段',
    icon: FileJson,
    endpoint: '/api/export/json',
    mimeType: 'application/json',
    ext: 'json',
  },
  {
    id: 'markdown',
    label: 'Markdown',
    description: '格式化文档，含目录、知识条目、实体表格和标签列表',
    icon: FileText,
    endpoint: '/api/export/markdown',
    mimeType: 'text/markdown',
    ext: 'md',
  },
  {
    id: 'csv-knowledge',
    label: 'CSV (知识)',
    description: '知识库表格导出，适合在Excel中查看',
    icon: FileSpreadsheet,
    endpoint: '/api/export/csv?table=knowledge',
    mimeType: 'text/csv',
    ext: 'csv',
  },
  {
    id: 'csv-entities',
    label: 'CSV (实体)',
    description: '实体表格导出',
    icon: FileSpreadsheet,
    endpoint: '/api/export/csv?table=entities',
    mimeType: 'text/csv',
    ext: 'csv',
  },
  {
    id: 'csv-tags',
    label: 'CSV (标签)',
    description: '标签表格导出',
    icon: FileSpreadsheet,
    endpoint: '/api/export/csv?table=tags',
    mimeType: 'text/csv',
    ext: 'csv',
  },
  {
    id: 'markdown-json',
    label: 'JSON+Markdown',
    description: 'JSON格式但内容为Markdown标记（兼容旧格式）',
    icon: File,
    endpoint: '/api/export/markdown-json',
    mimeType: 'application/json',
    ext: 'json',
  },
];

// ── Main Page ───────────────────────────────────────────────

export default function ExportPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [healthOk, setHealthOk] = useState(false);
  const [exporting, setExporting] = useState<string | null>(null);
  const [exportResult, setExportResult] = useState<{ format: string; ok: boolean; msg: string } | null>(null);
  const [userId, setUserId] = useState('');

  // Load user ID from localStorage
  useEffect(() => {
    try {
      const token = localStorage.getItem('opensoul-token');
      if (token) {
        // Try to decode JWT to get user ID
        const payload = JSON.parse(atob(token.split('.')[1]));
        setUserId(payload.sub || payload.user_id || '');
      }
    } catch {}
    // Also try to get from user store or API
    apiFetch('/api/user/me').then((data) => {
      if (data.id) setUserId(data.id);
    }).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const handleExport = async (format: ExportFormat) => {
    if (!userId) {
      setError('请先设置用户ID');
      return;
    }
    try {
      setExporting(format.id);
      setExportResult(null);
      setError('');

      const separator = format.endpoint.includes('?') ? '&' : '?';
      const url = `${format.endpoint}${separator}user_id=${encodeURIComponent(userId)}`;

      const response = await fetch(url, {
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('opensoul-token') || ''}`,
        },
      });

      if (!response.ok) {
        const errText = await response.text();
        throw new Error(errText || `HTTP ${response.status}`);
      }

      // Download the file
      const blob = await response.blob();
      const downloadUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = downloadUrl;
      const timestamp = new Date().toISOString().slice(0, 10);
      a.download = `opensoul-export-${format.id}-${timestamp}.${format.ext}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(downloadUrl);

      setExportResult({ format: format.id, ok: true, msg: `${format.label} 导出成功` });
    } catch (e: any) {
      setExportResult({ format: format.id, ok: false, msg: e.message });
    } finally {
      setExporting(null);
    }
  };

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* ── Header Info ── */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <Database className="w-3.5 h-3.5 text-blue-500" />
            <span className="text-xs text-muted-foreground">知识</span>
          </div>
          <div className="text-lg font-bold">导出为多种格式</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <Layers className="w-3.5 h-3.5 text-violet-500" />
            <span className="text-xs text-muted-foreground">实体</span>
          </div>
          <div className="text-lg font-bold">结构化数据</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <Tag className="w-3.5 h-3.5 text-amber-500" />
            <span className="text-xs text-muted-foreground">标签</span>
          </div>
          <div className="text-lg font-bold">分类体系</div>
        </div>
      </div>

      {/* ── User ID Input ── */}
      <div className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-center gap-2 mb-3">
          <Activity className="w-4 h-4" />
          <span className="text-sm font-medium">导出配置</span>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex-1">
            <label className="text-xs text-muted-foreground">用户ID（UUID）</label>
            <input
              className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              placeholder="输入用户UUID..."
            />
          </div>
          <div className="text-[10px] text-muted-foreground max-w-[200px]">
            从JWT token自动获取，也可手动输入。导出的数据仅包含该用户的内容。
          </div>
        </div>
      </div>

      {/* ── Export Formats ── */}
      <div className="rounded-lg border border-border bg-card">
        <div className="flex items-center gap-2 p-4 border-b border-border">
          <Download className="w-4 h-4" />
          <span className="text-sm font-medium">选择导出格式</span>
        </div>
        <div className="divide-y divide-border">
          {FORMATS.map((format) => {
            const Icon = format.icon;
            const isExporting = exporting === format.id;
            const result = exportResult?.format === format.id ? exportResult : null;

            return (
              <div key={format.id} className="flex items-center gap-3 p-4 hover:bg-muted/30 transition-colors">
                <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                  <Icon className="w-5 h-5 text-primary" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{format.label}</span>
                    <Badge variant="muted" className="font-mono">
                      .{format.ext}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground mt-0.5">{format.description}</p>
                  {result && (
                    <div className={`mt-1 flex items-center gap-1 text-[10px] ${result.ok ? 'text-emerald-500' : 'text-red-500'}`}>
                      {result.ok ? <CheckCircle className="w-3 h-3" /> : <AlertCircle className="w-3 h-3" />}
                      {result.msg}
                    </div>
                  )}
                </div>
                <Button onClick={() => handleExport(format)}
                  disabled={isExporting || !userId} variant="primary" className="gap-1.5 shrink-0">
                  {isExporting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
                  {isExporting ? '导出中...' : '导出'}
                </Button>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Info ── */}
      <div className="rounded-lg border border-blue-500/30 bg-blue-500/5 p-3">
        <div className="flex items-center gap-2 mb-1">
          <Database className="w-4 h-4 text-blue-500" />
          <span className="text-xs font-medium text-blue-500">数据导出说明</span>
        </div>
        <ul className="text-[10px] text-muted-foreground space-y-0.5 ml-6">
          <li>• JSON格式包含完整的知识条目、实体和标签数据</li>
          <li>• Markdown格式生成可读的文档，含目录和格式化内容</li>
          <li>• CSV格式适合在Excel/WPS中打开和分析</li>
          <li>• 导出数据仅包含当前用户的内容，不影响其他用户</li>
          <li>• 所有导出操作为只读，不会修改任何数据</li>
        </ul>
      </div>

      {/* ── Error ── */}
      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3 flex items-center gap-2">
          <AlertCircle className="w-4 h-4 text-red-500 shrink-0" />
          <span className="text-xs text-red-500 flex-1">{error}</span>
          <Button onClick={() => setError('')} variant="icon-sm" className="hover:bg-muted"><X className="w-3 h-3" /></Button>
        </div>
      )}
    </div>
  );
}
