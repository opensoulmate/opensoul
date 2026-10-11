'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  RefreshCw, X, Eye, BarChart3, LineChart, PieChart, ScatterChart,
  Trash2, Download, Loader2, AlertCircle, CheckCircle, Network,
  Activity, FileImage, Settings, Plus, Minus,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

// ── Types ───────────────────────────────────────────────────

interface VisionStats {
  status: string;
  component: string;
  total_charts: number;
  total_errors: number;
  engine: string;
  saved_outputs: number;
  output_dir: string;
}

interface SavedOutput {
  filename: string;
  size_bytes: number;
  created_at: string;
}

type ChartType = 'bar' | 'line' | 'pie' | 'scatter' | 'mindmap';

// ── Main Page ───────────────────────────────────────────────

export default function VisionPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [stats, setStats] = useState<VisionStats | null>(null);
  const [outputs, setOutputs] = useState<SavedOutput[]>([]);
  const [activeTab, setActiveTab] = useState<ChartType>('bar');

  // Chart form state
  const [generating, setGenerating] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewMeta, setPreviewMeta] = useState<Record<string, any> | null>(null);

  // Bar chart
  const [barLabels, setBarLabels] = useState('一月,二月,三月,四月,五月');
  const [barValues, setBarValues] = useState('10,25,18,32,22');
  const [barTitle, setBarTitle] = useState('');
  const [barColor, setBarColor] = useState('#e11d48');

  // Line chart
  const [lineX, setLineX] = useState('1,2,3,4,5,6');
  const [lineSeries, setLineSeries] = useState('系列A:10,15,13,18,22,20');
  const [lineTitle, setLineTitle] = useState('');

  // Pie chart
  const [pieLabels, setPieLabels] = useState('Python,JavaScript,Rust,Go');
  const [pieValues, setPieValues] = useState('40,30,15,15');
  const [pieTitle, setPieTitle] = useState('');

  // Scatter
  const [scatterX, setScatterX] = useState('1,2,3,4,5,6,7,8');
  const [scatterY, setScatterY] = useState('2,4,5,4,6,8,7,9');
  const [scatterTitle, setScatterTitle] = useState('');

  // Mindmap
  const [mindmapJson, setMindmapJson] = useState(JSON.stringify({
    label: '知识大脑',
    children: [
      { label: 'Raw层', children: [{ label: '执行轨迹' }, { label: '反馈提取' }] },
      { label: 'Wiki层', children: [{ label: '知识累积' }, { label: '模式发现' }] },
      { label: 'Skill层', children: [{ label: '技能进化' }, { label: '验证门控' }] },
    ],
  }, null, 2));
  const [mindmapTitle, setMindmapTitle] = useState('知识大脑架构');
  const [mindmapLayout, setMindmapLayout] = useState('radial');

  const fetchData = useCallback(async () => {
    setLoading(true);
    const results = await Promise.allSettled([
      apiFetch('/api/vision/stats'),
      apiFetch('/api/vision/outputs'),
    ]);
    if (results[0].status === 'fulfilled') setStats(results[0].value);
    if (results[1].status === 'fulfilled') setOutputs(results[1].value.outputs || []);
    setLoading(false);
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleGenerate = async () => {
    try {
      setGenerating(true);
      setError('');
      setPreviewUrl(null);
      setPreviewMeta(null);

      let url = '';
      let body: any = {};

      if (activeTab === 'bar') {
        url = '/api/vision/chart/bar/json';
        body = {
          labels: barLabels.split(',').map(s => s.trim()),
          values: barValues.split(',').map(s => parseFloat(s.trim())),
          title: barTitle,
          color: barColor,
          save_output: true,
        };
      } else if (activeTab === 'line') {
        url = '/api/vision/chart/line/json';
        const seriesObj: Record<string, number[]> = {};
        lineSeries.split(';').forEach(s => {
          const [name, vals] = s.split(':');
          if (name && vals) {
            seriesObj[name.trim()] = vals.split(',').map(v => parseFloat(v.trim()));
          }
        });
        body = {
          x: lineX.split(',').map(s => s.trim()),
          series: seriesObj,
          title: lineTitle,
          save_output: true,
        };
      } else if (activeTab === 'pie') {
        url = '/api/vision/chart/pie/json';
        body = {
          labels: pieLabels.split(',').map(s => s.trim()),
          values: pieValues.split(',').map(s => parseFloat(s.trim())),
          title: pieTitle,
          save_output: true,
        };
      } else if (activeTab === 'scatter') {
        url = '/api/vision/chart/scatter/json';
        body = {
          x: scatterX.split(',').map(s => parseFloat(s.trim())),
          y: scatterY.split(',').map(s => parseFloat(s.trim())),
          title: scatterTitle,
          save_output: true,
        };
      } else if (activeTab === 'mindmap') {
        url = '/api/vision/mindmap/json';
        body = {
          root: JSON.parse(mindmapJson),
          title: mindmapTitle,
          layout: mindmapLayout,
          save_output: true,
        };
      }

      const data = await apiFetch(url, { method: 'POST', body: JSON.stringify(body) });

      if (data.image_base64) {
        const fmt = data.format === 'png' ? 'image/png' : 'image/svg+xml';
        setPreviewUrl(`data:${fmt};base64,${data.image_base64}`);
      }
      setPreviewMeta(data);
      fetchData(); // refresh outputs list
    } catch (e: any) {
      setError(e.message);
    } finally {
      setGenerating(false);
    }
  };

  const handleDeleteOutput = async (filename: string) => {
    if (!confirm(`确定要删除「${filename}」？`)) return;
    try {
      await apiFetch(`/api/vision/outputs/${encodeURIComponent(filename)}`, { method: 'DELETE' });
      fetchData();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const TABS: { id: ChartType; label: string; icon: typeof BarChart3 }[] = [
    { id: 'bar', label: '柱状图', icon: BarChart3 },
    { id: 'line', label: '折线图', icon: LineChart },
    { id: 'pie', label: '饼图', icon: PieChart },
    { id: 'scatter', label: '散点图', icon: ScatterChart },
    { id: 'mindmap', label: '思维导图', icon: Network },
  ];

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* ── Stats ── */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <Activity className="w-3.5 h-3.5 text-green-500" />
            <span className="text-xs text-muted-foreground">状态</span>
          </div>
          <div className="text-lg font-bold">{stats?.status === 'ok' ? '正常' : '未知'}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{stats?.total_charts || 0}</div>
          <div className="text-xs text-muted-foreground">已生成图表</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{stats?.saved_outputs || 0}</div>
          <div className="text-xs text-muted-foreground">已保存文件</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{stats?.total_errors || 0}</div>
          <div className="text-xs text-muted-foreground">错误数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-sm font-mono truncate">{stats?.engine || '-'}</div>
          <div className="text-xs text-muted-foreground">渲染引擎</div>
        </div>
      </div>

      {/* ── Chart Type Tabs ── */}
      <div className="flex items-center gap-1 p-1 bg-muted rounded-lg">
        {TABS.map((tab) => {
          const Icon = tab.icon;
          return (
            <Button variant="bare"
              key={tab.id}
              onClick={() => { setActiveTab(tab.id); setPreviewUrl(null); setPreviewMeta(null); }}
              className={`flex items-center gap-1.5 px-3 py-2 text-xs lg:text-sm rounded-lg transition-colors ${
                activeTab === tab.id
                  ? 'bg-primary/10 text-primary font-medium'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              {tab.label}
            </Button>
          );
        })}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* ── Form ── */}
        <div className="rounded-lg border border-border bg-card">
          <div className="flex items-center gap-2 p-4 border-b border-border">
            <Settings className="w-4 h-4" />
            <span className="text-sm font-medium">图表配置</span>
          </div>
          <div className="p-4 space-y-3">
            {activeTab === 'bar' && (
              <>
                <div>
                  <label className="text-xs text-muted-foreground">标签（逗号分隔）</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                    value={barLabels} onChange={e => setBarLabels(e.target.value)} placeholder="一月,二月,三月" />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">数值（逗号分隔）</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                    value={barValues} onChange={e => setBarValues(e.target.value)} placeholder="10,20,30" />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">标题</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                    value={barTitle} onChange={e => setBarTitle(e.target.value)} placeholder="图表标题" />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">颜色</label>
                  <div className="flex items-center gap-2 mt-1">
                    <input type="color" value={barColor} onChange={e => setBarColor(e.target.value)} className="w-8 h-8 rounded cursor-pointer" />
                    <input className="flex-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono"
                      value={barColor} onChange={e => setBarColor(e.target.value)} />
                  </div>
                </div>
              </>
            )}
            {activeTab === 'line' && (
              <>
                <div>
                  <label className="text-xs text-muted-foreground">X轴值（逗号分隔）</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                    value={lineX} onChange={e => setLineX(e.target.value)} />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">数据系列（名称:值1,值2,...; 多系列用分号分隔）</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                    value={lineSeries} onChange={e => setLineSeries(e.target.value)} />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">标题</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                    value={lineTitle} onChange={e => setLineTitle(e.target.value)} />
                </div>
              </>
            )}
            {activeTab === 'pie' && (
              <>
                <div>
                  <label className="text-xs text-muted-foreground">标签（逗号分隔）</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                    value={pieLabels} onChange={e => setPieLabels(e.target.value)} />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">数值（逗号分隔）</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                    value={pieValues} onChange={e => setPieValues(e.target.value)} />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">标题</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                    value={pieTitle} onChange={e => setPieTitle(e.target.value)} />
                </div>
              </>
            )}
            {activeTab === 'scatter' && (
              <>
                <div>
                  <label className="text-xs text-muted-foreground">X值（逗号分隔）</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                    value={scatterX} onChange={e => setScatterX(e.target.value)} />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">Y值（逗号分隔）</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                    value={scatterY} onChange={e => setScatterY(e.target.value)} />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">标题</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                    value={scatterTitle} onChange={e => setScatterTitle(e.target.value)} />
                </div>
              </>
            )}
            {activeTab === 'mindmap' && (
              <>
                <div>
                  <label className="text-xs text-muted-foreground">根节点JSON（label + children递归结构）</label>
                  <textarea className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono h-40 resize-y"
                    value={mindmapJson} onChange={e => setMindmapJson(e.target.value)} />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">标题</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                    value={mindmapTitle} onChange={e => setMindmapTitle(e.target.value)} />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">布局</label>
                  <select className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                    value={mindmapLayout} onChange={e => setMindmapLayout(e.target.value)}>
                    <option value="radial">放射状</option>
                    <option value="tree">树状</option>
                  </select>
                </div>
              </>
            )}

            <Button onClick={handleGenerate}
              disabled={generating} variant="primary" className="w-full gap-1.5 px-3">
              {generating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Eye className="w-3.5 h-3.5" />}
              {generating ? '生成中...' : '生成图表'}
            </Button>
          </div>
        </div>

        {/* ── Preview ── */}
        <div className="rounded-lg border border-border bg-card">
          <div className="flex items-center gap-2 p-4 border-b border-border">
            <Eye className="w-4 h-4" />
            <span className="text-sm font-medium">预览</span>
            {previewMeta && (
              <span className="text-[10px] text-muted-foreground ml-auto">
                {previewMeta.engine} · {previewMeta.format} · {previewMeta.elapsed_ms || 0}ms
                {previewMeta.size_bytes ? ` · ${(previewMeta.size_bytes / 1024).toFixed(1)}KB` : ''}
              </span>
            )}
          </div>
          <div className="p-4 flex items-center justify-center min-h-[300px]">
            {previewUrl ? (
              <img src={previewUrl} alt="Chart preview" className="max-w-full max-h-[400px] object-contain rounded" />
            ) : (
              <div className="text-center text-muted-foreground">
                <FileImage className="w-12 h-12 mx-auto mb-2 opacity-30" />
                <p className="text-xs">配置参数后点击"生成图表"预览</p>
              </div>
            )}
          </div>
          {previewMeta?.output_file && (
            <div className="px-4 pb-3">
              <Badge variant="success">
                已保存: {previewMeta.output_file}
              </Badge>
            </div>
          )}
        </div>
      </div>

      {/* ── Saved Outputs ── */}
      <div className="rounded-lg border border-border bg-card">
        <div className="flex items-center gap-2 p-4 border-b border-border">
          <FileImage className="w-4 h-4" />
          <span className="text-sm font-medium">已保存文件</span>
          <span className="text-[10px] text-muted-foreground ml-auto">{outputs.length} 个文件</span>
        </div>
        {outputs.length === 0 ? (
          <div className="p-8 text-center text-muted-foreground">
            <FileImage className="w-8 h-8 mx-auto mb-2 opacity-30" />
            <p className="text-xs">暂无保存的文件</p>
          </div>
        ) : (
          <div className="divide-y divide-border max-h-[300px] overflow-auto">
            {outputs.map((f) => (
              <div key={f.filename} className="flex items-center gap-3 px-3 py-2 hover:bg-muted/30">
                <FileImage className="w-4 h-4 text-muted-foreground shrink-0" />
                <div className="flex-1 min-w-0">
                  <span className="text-xs font-mono truncate block">{f.filename}</span>
                  <span className="text-[10px] text-muted-foreground">
                    {(f.size_bytes / 1024).toFixed(1)} KB
                    {f.created_at && ` · ${new Date(f.created_at).toLocaleString('zh-CN')}`}
                  </span>
                </div>
                <Button
                  onClick={() => handleDeleteOutput(f.filename)}
                  variant="chip-xs" className="bg-red-500/10 text-red-500 hover:bg-red-500/20 shrink-0"
                >
                  <Trash2 className="w-3 h-3" />
                </Button>
              </div>
            ))}
          </div>
        )}
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
