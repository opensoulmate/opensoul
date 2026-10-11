'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Plus, Trash2, RefreshCw, X, Brain, Sparkles, Heart,
  CheckCircle, Edit3, Save, ChevronRight, BarChart3, Zap, Star
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

interface Personality {
  personality_id: string;
  name: string;
  description: string;
  tone: string;
  language_style: string;
  emoji_usage: string;
  response_length: string;
  traits: string[];
  builtin: boolean;
  usage_count: number;
}

interface EmotionResult {
  primary_emotion: string;
  confidence: number;
  emotions: Record<string, number>;
  valence: number;
  arousal: number;
  sentiment: string;
  keywords: string[];
  elapsed_ms: number;
}

const TONES = ['neutral', 'friendly', 'professional', 'humorous', 'caring', 'energetic', 'calm'];
const STYLES = ['normal', 'formal', 'casual', 'technical', 'poetic'];
const EMOJI_LEVELS = ['none', 'minimal', 'moderate', 'frequent', 'excessive'];
const LENGTHS = ['concise', 'normal', 'detailed', 'verbose'];

const EMOTION_COLORS: Record<string, string> = {
  joy: 'text-yellow-500', happiness: 'text-yellow-500',
  sadness: 'text-blue-500', anger: 'text-red-500',
  fear: 'text-purple-500', surprise: 'text-orange-500',
  disgust: 'text-green-600', trust: 'text-teal-500',
  anticipation: 'text-amber-500', love: 'text-pink-500',
};

export default function MindPage() {
  const [personalities, setPersonalities] = useState<Personality[]>([]);
  const [activeId, setActiveId] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [stats, setStats] = useState<any>(null);

  // Create/Edit dialog
  const [showDialog, setShowDialog] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState({
    name: '', description: '', tone: 'neutral', language_style: 'normal',
    emoji_usage: 'moderate', response_length: 'normal', traits: '',
    system_prompt_suffix: '',
  });
  const [saving, setSaving] = useState(false);

  // Detail dialog
  const [selected, setSelected] = useState<Personality | null>(null);
  const [showDetail, setShowDetail] = useState(false);

  // Emotion test
  const [emotionText, setEmotionText] = useState('');
  const [emotionResult, setEmotionResult] = useState<EmotionResult | null>(null);
  const [emotionLoading, setEmotionLoading] = useState(false);
  const [showEmotion, setShowEmotion] = useState(false);

  const fetchAll = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiFetch('/api/mind/personalities');
      setPersonalities(Array.isArray(data.personalities) ? data.personalities : []);
      setActiveId(data.active || '');
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchStats = useCallback(async () => {
    try {
      const data = await apiFetch('/api/mind/stats');
      setStats(data);
    } catch { /* silent */ }
  }, []);

  useEffect(() => { fetchAll(); fetchStats(); }, [fetchAll, fetchStats]);

  const handleSetActive = async (pid: string) => {
    try {
      await apiFetch('/api/mind/personalities/active', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ personality_id: pid }),
      });
      setActiveId(pid);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleDelete = async (pid: string) => {
    const p = personalities.find(x => x.personality_id === pid);
    if (p?.builtin) return;
    if (!confirm(`确定删除人格「${p?.name || pid}」？`)) return;
    try {
      await apiFetch(`/api/mind/personalities/${pid}`, { method: 'DELETE' });
      await fetchAll();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const openCreate = () => {
    setEditId(null);
    setForm({ name: '', description: '', tone: 'neutral', language_style: 'normal', emoji_usage: 'moderate', response_length: 'normal', traits: '', system_prompt_suffix: '' });
    setShowDialog(true);
  };

  const openEdit = async (pid: string) => {
    try {
      const data = await apiFetch(`/api/mind/personalities/${pid}`);
      setEditId(pid);
      setForm({
        name: data.name || '', description: data.description || '',
        tone: data.tone || 'neutral', language_style: data.language_style || 'normal',
        emoji_usage: data.emoji_usage || 'moderate', response_length: data.response_length || 'normal',
        traits: (data.traits || []).join(', '), system_prompt_suffix: data.system_prompt_suffix || '',
      });
      setShowDialog(true);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleSave = async () => {
    try {
      setSaving(true);
      const body = {
        name: form.name,
        description: form.description,
        tone: form.tone,
        language_style: form.language_style,
        emoji_usage: form.emoji_usage,
        response_length: form.response_length,
        traits: form.traits.split(',').map(t => t.trim()).filter(Boolean),
        system_prompt_suffix: form.system_prompt_suffix,
      };
      if (editId) {
        await apiFetch(`/api/mind/personalities/${editId}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
      } else {
        await apiFetch('/api/mind/personalities', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
      }
      setShowDialog(false);
      await fetchAll();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleAnalyzeEmotion = async () => {
    if (!emotionText.trim()) return;
    try {
      setEmotionLoading(true);
      const data = await apiFetch('/api/mind/emotion/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: emotionText }),
      });
      setEmotionResult(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setEmotionLoading(false);
    }
  };

  const filtered = personalities.filter(p => {
    if (!search) return true;
    const q = search.toLowerCase();
    return p.name.toLowerCase().includes(q) || p.description.toLowerCase().includes(q) ||
      p.traits.some(t => t.toLowerCase().includes(q));
  });

  const builtinCount = personalities.filter(p => p.builtin).length;
  const customCount = personalities.filter(p => !p.builtin).length;

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-500/10 text-red-500 text-xs">
          {error}
          <button onClick={() => setError('')} className="ml-auto"><X className="w-3.5 h-3.5" /></button>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{personalities.length}</div>
          <div className="text-xs text-muted-foreground">人格总数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-500">{builtinCount}</div>
          <div className="text-xs text-muted-foreground">内置人格</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-600">{customCount}</div>
          <div className="text-xs text-muted-foreground">自定义人格</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-purple-500">{activeId ? '✓' : '—'}</div>
          <div className="text-xs text-muted-foreground">当前激活</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索人格名称、描述、特质..."
            value={search} onChange={e => setSearch(e.target.value)}
          />
        </div>
        <button onClick={() => setShowEmotion(true)} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm bg-violet-500/10 text-violet-500 border border-border rounded-lg hover:bg-violet-500/20">
          <Sparkles className="w-3.5 h-3.5" />情绪分析
        </button>
        <button onClick={openCreate} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm bg-primary/10 text-primary border border-border rounded-lg hover:bg-primary/20">
          <Plus className="w-3.5 h-3.5" />新建人格
        </button>
        <Button onClick={() => { fetchAll(); fetchStats(); }} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
      </div>

      {/* Personality Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {filtered.map(p => {
          const isActive = p.personality_id === activeId;
          return (
            <div
              key={p.personality_id}
              className={`rounded-lg border bg-card p-4 hover:bg-muted/30 transition-colors cursor-pointer ${isActive ? 'border-primary/50 ring-1 ring-primary/20' : 'border-border'}`}
              onClick={() => { setSelected(p); setShowDetail(true); }}
            >
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-2">
                  <Brain className={`w-5 h-5 ${isActive ? 'text-primary' : 'text-muted-foreground'}`} />
                  <h3 className="text-sm font-medium">{p.name}</h3>
                  {p.builtin && <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-blue-500/10 text-blue-500">内置</span>}
                  {isActive && <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-primary/10 text-primary">激活</span>}
                </div>
                <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
                  {!p.builtin && (
                    <>
                      <button onClick={() => openEdit(p.personality_id)} className="p-1 text-muted-foreground hover:text-foreground rounded-md">
                        <Edit3 className="w-3.5 h-3.5" />
                      </button>
                      <button onClick={() => handleDelete(p.personality_id)} className="p-1 text-muted-foreground hover:text-red-500 rounded-md">
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </>
                  )}
                </div>
              </div>
              <p className="text-xs text-muted-foreground mt-2 line-clamp-2">{p.description || '暂无描述'}</p>
              <div className="flex flex-wrap gap-1.5 mt-2">
                <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-muted">{p.tone}</span>
                <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-muted">{p.language_style}</span>
                <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-muted">emoji: {p.emoji_usage}</span>
              </div>
              {p.traits.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-2">
                  {p.traits.slice(0, 4).map(t => (
                    <span key={t} className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-primary/5 text-primary/70">{t}</span>
                  ))}
                  {p.traits.length > 4 && <span className="text-[10px] text-muted-foreground">+{p.traits.length - 4}</span>}
                </div>
              )}
              <div className="flex items-center justify-between mt-3 pt-3 border-t border-border" onClick={e => e.stopPropagation()}>
                <span className="text-[10px] text-muted-foreground">使用 {p.usage_count} 次</span>
                {!isActive && (
                  <button onClick={() => handleSetActive(p.personality_id)} className="text-[10px] px-2 py-1 bg-primary/10 text-primary rounded-lg hover:bg-primary/20">
                    设为激活
                  </button>
                )}
              </div>
            </div>
          );
        })}
        {filtered.length === 0 && (
          <div className="col-span-full text-center text-sm text-muted-foreground py-8">暂无人格数据</div>
        )}
      </div>

      {/* Stats Detail */}
      {stats && (
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-center gap-2 mb-3">
            <BarChart3 className="w-4 h-4 text-muted-foreground" />
            <h3 className="text-sm font-medium">OpenMind 统计</h3>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            <div><span className="text-muted-foreground">活跃人格: </span><span className="font-medium">{stats.active_personality || '—'}</span></div>
            {stats.by_tone && Object.entries(stats.by_tone).map(([tone, cnt]) => (
              <div key={tone}><span className="text-muted-foreground">{tone}: </span><span className="font-medium">{String(cnt)}</span></div>
            ))}
          </div>
          {stats.emotion && (
            <div className="mt-2 text-xs text-muted-foreground">
              情绪引擎: {JSON.stringify(stats.emotion)}
            </div>
          )}
        </div>
      )}

      {/* Detail Dialog */}
      {showDetail && selected && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowDetail(false)}>
          <div className="bg-card rounded-lg border border-border p-6 max-w-lg w-full mx-4 max-h-[80vh] overflow-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <Brain className="w-5 h-5 text-primary" />
                <h2 className="text-lg font-medium">{selected.name}</h2>
                {selected.builtin && <Badge variant="info">内置</Badge>}
              </div>
              <button onClick={() => setShowDetail(false)}><X className="w-4 h-4 text-muted-foreground" /></button>
            </div>
            <div className="space-y-3 text-sm">
              <div><span className="text-muted-foreground">描述: </span>{selected.description || '暂无'}</div>
              <div className="grid grid-cols-2 gap-2">
                <div><span className="text-muted-foreground">语调: </span>{selected.tone}</div>
                <div><span className="text-muted-foreground">语言风格: </span>{selected.language_style}</div>
                <div><span className="text-muted-foreground">Emoji使用: </span>{selected.emoji_usage}</div>
                <div><span className="text-muted-foreground">回复长度: </span>{selected.response_length}</div>
              </div>
              {selected.traits.length > 0 && (
                <div>
                  <span className="text-muted-foreground">特质: </span>
                  <div className="flex flex-wrap gap-1 mt-1">
                    {selected.traits.map(t => <Badge key={t} variant="subtle">{t}</Badge>)}
                  </div>
                </div>
              )}
              <div><span className="text-muted-foreground">使用次数: </span>{selected.usage_count}</div>
              <div><span className="text-muted-foreground">人格ID: </span><code className="text-xs bg-muted px-1.5 py-0.5 rounded">{selected.personality_id}</code></div>
            </div>
            <div className="flex justify-end gap-2 mt-6">
              {!selected.builtin && <Button onClick={() => { setShowDetail(false); openEdit(selected.personality_id); }} variant="secondary">编辑</Button>}
              {selected.personality_id !== activeId && (
                <button onClick={() => { handleSetActive(selected.personality_id); setShowDetail(false); }} className="px-3 py-1.5 text-xs lg:text-sm bg-primary/10 text-primary border border-border rounded-lg hover:bg-primary/20">设为激活</button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Create/Edit Dialog */}
      {showDialog && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowDialog(false)}>
          <div className="bg-card rounded-lg border border-border p-6 max-w-lg w-full mx-4 max-h-[80vh] overflow-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-medium">{editId ? '编辑人格' : '新建人格'}</h2>
              <button onClick={() => setShowDialog(false)}><X className="w-4 h-4 text-muted-foreground" /></button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">名称 *</label>
                <input className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="人格名称" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">描述</label>
                <textarea className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 h-20 resize-none" value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} placeholder="人格描述" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">语调</label>
                  <select className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md" value={form.tone} onChange={e => setForm({ ...form, tone: e.target.value })}>
                    {TONES.map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">语言风格</label>
                  <select className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md" value={form.language_style} onChange={e => setForm({ ...form, language_style: e.target.value })}>
                    {STYLES.map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">Emoji使用</label>
                  <select className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md" value={form.emoji_usage} onChange={e => setForm({ ...form, emoji_usage: e.target.value })}>
                    {EMOJI_LEVELS.map(e => <option key={e} value={e}>{e}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">回复长度</label>
                  <select className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md" value={form.response_length} onChange={e => setForm({ ...form, response_length: e.target.value })}>
                    {LENGTHS.map(l => <option key={l} value={l}>{l}</option>)}
                  </select>
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">特质（逗号分隔）</label>
                <input className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={form.traits} onChange={e => setForm({ ...form, traits: e.target.value })} placeholder="耐心, 幽默, 严谨" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">系统提示词后缀</label>
                <textarea className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 h-16 resize-none" value={form.system_prompt_suffix} onChange={e => setForm({ ...form, system_prompt_suffix: e.target.value })} placeholder="追加到系统提示词的内容" />
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-6">
              <Button onClick={() => setShowDialog(false)} variant="secondary">取消</Button>
              <Button onClick={handleSave} disabled={saving || !form.name.trim()} variant="primary" className="gap-1.5">
                {saving ? <Zap className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                {saving ? '保存中...' : '保存'}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Emotion Analysis Dialog */}
      {showEmotion && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowEmotion(false)}>
          <div className="bg-card rounded-lg border border-border p-6 max-w-lg w-full mx-4" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <Sparkles className="w-5 h-5 text-purple-500" />
                <h2 className="text-lg font-medium">情绪分析</h2>
              </div>
              <button onClick={() => setShowEmotion(false)}><X className="w-4 h-4 text-muted-foreground" /></button>
            </div>
            <div className="space-y-3">
              <textarea
                className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 h-24 resize-none"
                value={emotionText} onChange={e => setEmotionText(e.target.value)}
                placeholder="输入要分析的文本..."
              />
              <button onClick={handleAnalyzeEmotion} disabled={emotionLoading || !emotionText.trim()} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm bg-violet-500/10 text-violet-500 border border-border rounded-lg hover:bg-violet-500/20 disabled:opacity-50">
                {emotionLoading ? <Zap className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
                {emotionLoading ? '分析中...' : '开始分析'}
              </button>
              {emotionResult && (
                <div className="space-y-3 mt-3 p-3 rounded-lg bg-muted/50">
                  <div className="flex items-center gap-2">
                    <span className="text-lg">{emotionResult.sentiment === 'positive' ? '😊' : emotionResult.sentiment === 'negative' ? '😔' : '😐'}</span>
                    <div>
                      <div className="text-sm font-medium">{emotionResult.primary_emotion}</div>
                      <div className="text-xs text-muted-foreground">置信度 {(emotionResult.confidence * 100).toFixed(1)}% · 情感 {emotionResult.sentiment}</div>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <div><span className="text-muted-foreground">效价: </span>{emotionResult.valence.toFixed(2)}</div>
                    <div><span className="text-muted-foreground">唤醒度: </span>{emotionResult.arousal.toFixed(2)}</div>
                  </div>
                  {Object.keys(emotionResult.emotions).length > 0 && (
                    <div>
                      <div className="text-xs text-muted-foreground mb-1">情绪分布:</div>
                      <div className="space-y-1">
                        {Object.entries(emotionResult.emotions).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([emo, score]) => (
                          <div key={emo} className="flex items-center gap-2">
                            <span className={`text-xs w-16 truncate ${EMOTION_COLORS[emo] || 'text-foreground'}`}>{emo}</span>
                            <div className="flex-1 bg-muted rounded-full h-1.5">
                              <div className="bg-primary h-1.5 rounded-full" style={{ width: `${(score as number) * 100}%` }} />
                            </div>
                            <span className="text-[10px] text-muted-foreground w-8 text-right">{((score as number) * 100).toFixed(0)}%</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {emotionResult.keywords.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {emotionResult.keywords.map(k => <span key={k} className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-violet-500/10 text-violet-500">{k}</span>)}
                    </div>
                  )}
                  <div className="text-[10px] text-muted-foreground">耗时 {emotionResult.elapsed_ms}ms</div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
