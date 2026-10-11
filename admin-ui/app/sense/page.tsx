'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Eye, FileText, Mic, Image, Video, RefreshCw, X, Upload,
  CheckCircle, XCircle, AlertTriangle, Loader2, Settings,
  Activity, Zap, Globe, Languages, Monitor,
} from 'lucide-react';
import { Button } from '@/components/ui/button';

// ── Types ───────────────────────────────────────────────────

interface EngineStatus {
  available: boolean;
  engine: string;
  languages?: string[];
  model?: string;
  whisper_local?: boolean;
  llm_fallback?: boolean;
}

interface HealthData {
  status: string;
  component: string;
  engines: {
    ocr: EngineStatus;
    asr: EngineStatus;
    multimodal: EngineStatus;
  };
}

interface StatsData {
  status: string;
  component: string;
  [key: string]: any;
}

// ── Engine Card ─────────────────────────────────────────────

function EngineCard({ name, icon: Icon, engine, color }: {
  name: string;
  icon: typeof Eye;
  engine: EngineStatus;
  color: string;
}) {
  const colorMap: Record<string, { bg: string; text: string; border: string }> = {
    blue: { bg: 'bg-blue-500/10', text: 'text-blue-500', border: 'border-blue-500/30' },
    green: { bg: 'bg-emerald-500/10', text: 'text-emerald-500', border: 'border-emerald-500/30' },
    purple: { bg: 'bg-violet-500/10', text: 'text-violet-500', border: 'border-violet-500/30' },
  };
  const c = colorMap[color] || colorMap.blue;

  return (
    <div className={`rounded-lg border ${c.border} bg-card p-4`}>
      <div className="flex items-center gap-3 mb-3">
        <div className={`w-10 h-10 rounded-lg ${c.bg} flex items-center justify-center`}>
          <Icon className={`w-5 h-5 ${c.text}`} />
        </div>
        <div className="flex-1">
          <div className="text-sm font-medium">{name}</div>
          <div className="text-[10px] text-muted-foreground font-mono">{engine.engine}</div>
        </div>
        {engine.available ? (
          <CheckCircle className="w-5 h-5 text-green-500" />
        ) : (
          <XCircle className="w-5 h-5 text-red-500" />
        )}
      </div>

      <div className="space-y-1.5 text-xs">
        <div className="flex justify-between">
          <span className="text-muted-foreground">状态</span>
          <span className={engine.available ? 'text-green-500' : 'text-red-500'}>
            {engine.available ? '可用' : '不可用'}
          </span>
        </div>
        {engine.model && (
          <div className="flex justify-between">
            <span className="text-muted-foreground">模型</span>
            <span className="font-mono">{engine.model}</span>
          </div>
        )}
        {engine.whisper_local !== undefined && (
          <div className="flex justify-between">
            <span className="text-muted-foreground">本地Whisper</span>
            <span>{engine.whisper_local ? '✅' : '❌'}</span>
          </div>
        )}
        {engine.llm_fallback !== undefined && (
          <div className="flex justify-between">
            <span className="text-muted-foreground">LLM回退</span>
            <span>{engine.llm_fallback ? '✅' : '❌'}</span>
          </div>
        )}
        {engine.languages && engine.languages.length > 0 && (
          <div className="flex justify-between">
            <span className="text-muted-foreground">语言</span>
            <span className="font-mono">{engine.languages.slice(0, 3).join(', ')}</span>
          </div>
        )}
      </div>
    </div>
  );
}

// ── OCR Panel ───────────────────────────────────────────────

function OCRPanel() {
  const [result, setResult] = useState('');
  const [loading, setLoading] = useState(false);
  const [languages, setLanguages] = useState<string[]>([]);
  const [selectedLang, setSelectedLang] = useState('chi_sim+eng');

  useEffect(() => {
    apiFetch('/api/sense/ocr/languages').then(d => {
      setLanguages(Array.isArray(d) ? d : d.languages || []);
    }).catch(() => {});
  }, []);

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>, mode: 'image' | 'pdf') => {
    const file = e.target.files?.[0];
    if (!file) return;

    setLoading(true);
    setResult('');
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('language', selectedLang);

      const endpoint = mode === 'image' ? '/api/sense/ocr/image' : '/api/sense/ocr/pdf';
      const token = localStorage.getItem('soul_token');
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: formData,
      });
      const data = await res.json();
      setResult(data.text || data.error || JSON.stringify(data, null, 2));
    } catch (err: any) {
      setResult(`错误: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <FileText className="w-4 h-4 text-blue-500" />
        <span className="text-sm font-medium">OCR 文字识别</span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
          value={selectedLang}
          onChange={(e) => setSelectedLang(e.target.value)}
        >
          <option value="chi_sim+eng">中文+英文</option>
          <option value="eng">英文</option>
          <option value="chi_sim">简体中文</option>
          <option value="chi_tra">繁体中文</option>
          <option value="jpn">日文</option>
          <option value="kor">韩文</option>
        </select>

        <label className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-md hover:opacity-90 cursor-pointer">
          <Upload className="w-3.5 h-3.5" />
          识别图片
          <input type="file" accept="image/*" className="hidden" onChange={(e) => handleFileUpload(e, 'image')} />
        </label>

        <label className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-muted border border-border rounded-md hover:bg-muted/80 cursor-pointer">
          <FileText className="w-3.5 h-3.5" />
          识别PDF
          <input type="file" accept=".pdf" className="hidden" onChange={(e) => handleFileUpload(e, 'pdf')} />
        </label>
      </div>

      {loading && (
        <div className="flex items-center gap-2 p-4 text-xs text-muted-foreground">
          <Loader2 className="w-4 h-4 animate-spin" /> 识别中...
        </div>
      )}

      {result && (
        <div className="rounded-lg border border-border bg-muted/30 p-3">
          <div className="text-xs text-muted-foreground mb-2">识别结果:</div>
          <pre className="text-xs whitespace-pre-wrap break-all max-h-[300px] overflow-auto">{result}</pre>
        </div>
      )}
    </div>
  );
}

// ── ASR Panel ───────────────────────────────────────────────

function ASRPanel() {
  const [result, setResult] = useState('');
  const [loading, setLoading] = useState(false);
  const [models, setModels] = useState<any[]>([]);
  const [selectedModel, setSelectedModel] = useState('base');

  useEffect(() => {
    apiFetch('/api/sense/asr/models').then(d => {
      setModels(Array.isArray(d) ? d : d.models || []);
    }).catch(() => {});
  }, []);

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setLoading(true);
    setResult('');
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('model_size', selectedModel);

      const token = localStorage.getItem('soul_token');
      const res = await fetch('/api/sense/asr/transcribe', {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: formData,
      });
      const data = await res.json();
      setResult(data.text || data.error || JSON.stringify(data, null, 2));
    } catch (err: any) {
      setResult(`错误: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Mic className="w-4 h-4 text-green-500" />
        <span className="text-sm font-medium">ASR 语音转写</span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
          value={selectedModel}
          onChange={(e) => setSelectedModel(e.target.value)}
        >
          <option value="tiny">Tiny (最快)</option>
          <option value="base">Base (平衡)</option>
          <option value="small">Small (较准)</option>
          <option value="medium">Medium (高精度)</option>
          <option value="large">Large (最高精度)</option>
        </select>

        <label className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-md hover:opacity-90 cursor-pointer">
          <Upload className="w-3.5 h-3.5" />
          上传音频
          <input type="file" accept="audio/*" className="hidden" onChange={handleFileUpload} />
        </label>
      </div>

      {loading && (
        <div className="flex items-center gap-2 p-4 text-xs text-muted-foreground">
          <Loader2 className="w-4 h-4 animate-spin" /> 转写中...
        </div>
      )}

      {result && (
        <div className="rounded-lg border border-border bg-muted/30 p-3">
          <div className="text-xs text-muted-foreground mb-2">转写结果:</div>
          <pre className="text-xs whitespace-pre-wrap break-all max-h-[300px] overflow-auto">{result}</pre>
        </div>
      )}
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────

export default function SensePage() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [stats, setStats] = useState<StatsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<'overview' | 'ocr' | 'asr'>('overview');

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const [h, s] = await Promise.allSettled([
        apiFetch('/api/sense/health'),
        apiFetch('/api/sense/stats'),
      ]);
      if (h.status === 'fulfilled') setHealth(h.value as HealthData);
      if (s.status === 'fulfilled') setStats(s.value as StatsData);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-1 bg-muted rounded-lg p-0.5">
          {[
            { id: 'overview' as const, label: '总览', icon: Monitor },
            { id: 'ocr' as const, label: 'OCR识别', icon: FileText },
            { id: 'asr' as const, label: '语音转写', icon: Mic },
          ].map((t) => {
            const Icon = t.icon;
            return (
              <Button
                key={t.id}
                onClick={() => setTab(t.id)}
                variant="chip-sm" className={`flex items-center gap-1.5 transition-colors  ${
                  tab === t.id ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                {t.label}
              </Button>
            );
          })}
        </div>
        <Button onClick={fetchData} variant="secondary" className="ml-auto">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-red-500" />
          <span className="text-xs text-red-500">{error}</span>
          <Button variant="bare" onClick={() => setError('')} className="ml-auto"><X className="w-3 h-3" /></Button>
        </div>
      )}

      {/* Overview */}
      {tab === 'overview' && health && (
        <div className="space-y-4">
          {/* Engine status */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <EngineCard name="OCR 文字识别" icon={FileText} engine={health.engines.ocr} color="blue" />
            <EngineCard name="ASR 语音转写" icon={Mic} engine={health.engines.asr} color="green" />
            <EngineCard name="多模态分析" icon={Eye} engine={health.engines.multimodal} color="purple" />
          </div>

          {/* Quick actions */}
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-center gap-2 mb-3">
              <Zap className="w-4 h-4" />
              <span className="text-sm font-medium">快速操作</span>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <Button variant="bare"
                onClick={() => setTab('ocr')}
                className="flex items-center gap-2 p-3 rounded-lg border border-border hover:bg-muted/30 transition-colors"
              >
                <FileText className="w-4 h-4 text-blue-500" />
                <div className="text-left">
                  <div className="text-xs font-medium">OCR识别</div>
                  <div className="text-[10px] text-muted-foreground">图片/PDF文字提取</div>
                </div>
              </Button>
              <Button variant="bare"
                onClick={() => setTab('asr')}
                className="flex items-center gap-2 p-3 rounded-lg border border-border hover:bg-muted/30 transition-colors"
              >
                <Mic className="w-4 h-4 text-green-500" />
                <div className="text-left">
                  <div className="text-xs font-medium">语音转写</div>
                  <div className="text-[10px] text-muted-foreground">音频→文字</div>
                </div>
              </Button>
              <div className="flex items-center gap-2 p-3 rounded-lg border border-border opacity-50">
                <Image className="w-4 h-4 text-purple-500" />
                <div className="text-left">
                  <div className="text-xs font-medium">图像分析</div>
                  <div className="text-[10px] text-muted-foreground">多模态理解</div>
                </div>
              </div>
              <div className="flex items-center gap-2 p-3 rounded-lg border border-border opacity-50">
                <Video className="w-4 h-4 text-amber-500" />
                <div className="text-left">
                  <div className="text-xs font-medium">视频分析</div>
                  <div className="text-[10px] text-muted-foreground">帧提取+分析</div>
                </div>
              </div>
            </div>
          </div>

          {/* Info */}
          <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 flex items-center gap-2">
            <Languages className="w-4 h-4 text-amber-500 shrink-0" />
            <div>
              <span className="text-xs font-medium text-amber-500">感官感知层</span>
              <p className="text-[10px] text-muted-foreground mt-0.5">
                OpenSense 提供 OCR (Tesseract)、ASR (Whisper)、多模态分析能力。
                当本地引擎不可用时，自动回退到 LLM 云端处理。
              </p>
            </div>
          </div>
        </div>
      )}

      {/* OCR Tab */}
      {tab === 'ocr' && (
        <div className="rounded-lg border border-border bg-card p-4">
          <OCRPanel />
        </div>
      )}

      {/* ASR Tab */}
      {tab === 'asr' && (
        <div className="rounded-lg border border-border bg-card p-4">
          <ASRPanel />
        </div>
      )}
    </div>
  );
}
