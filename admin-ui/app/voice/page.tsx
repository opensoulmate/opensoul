'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Mic, Plus, Trash2, RefreshCw, X, Save, Volume2, Play,
  Settings, Loader2, AlertCircle, CheckCircle, Headphones
} from 'lucide-react';
import { Tab, TabBar } from '@/components/ui/tab';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

interface VoiceProfile {
  profile_id: string;
  name: string;
  description: string;
  engine: string;
  voice_id: string;
  language: string;
  rate: string;
  pitch: string;
  volume: string;
  tags: string[];
  builtin: boolean;
  usage_count: number;
}

interface Voice {
  id: string;
  name: string;
  language: string;
  gender: string;
}

export default function VoicePage() {
  const [profiles, setProfiles] = useState<VoiceProfile[]>([]);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<'profiles' | 'voices'>('profiles');

  // Create profile dialog
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState({
    name: '', description: '', engine: 'edge-tts', voice_id: 'zh-CN-XiaoxiaoNeural',
    language: 'zh-CN', rate: '+0%', pitch: '+0Hz', volume: '+0%', tags: '',
  });
  const [creating, setCreating] = useState(false);

  // Test TTS
  const [testText, setTestText] = useState('你好，我是OpenSoul语音助手。');
  const [testProfile, setTestProfile] = useState('');
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<any>(null);

  // Edit
  const [editProfile, setEditProfile] = useState<VoiceProfile | null>(null);
  const [showEdit, setShowEdit] = useState(false);

  const fetchProfiles = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiFetch('/api/voice/profiles');
      setProfiles(Array.isArray(data.profiles) ? data.profiles : []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchVoices = useCallback(async () => {
    try {
      const data = await apiFetch('/api/voice/voices?language=zh');
      setVoices(Array.isArray(data.voices) ? data.voices : []);
    } catch (e: any) {
      // silent
    }
  }, []);

  useEffect(() => { fetchProfiles(); fetchVoices(); }, [fetchProfiles, fetchVoices]);

  const handleCreate = async () => {
    if (!createForm.name) { setError('名称必填'); return; }
    try {
      setCreating(true);
      await apiFetch('/api/voice/profiles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...createForm,
          tags: createForm.tags.split(',').map((t) => t.trim()).filter(Boolean),
        }),
      });
      setShowCreate(false);
      setCreateForm({ name: '', description: '', engine: 'edge-tts', voice_id: 'zh-CN-XiaoxiaoNeural', language: 'zh-CN', rate: '+0%', pitch: '+0Hz', volume: '+0%', tags: '' });
      fetchProfiles();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (profileId: string, builtin: boolean) => {
    if (builtin) { setError('内置角色不能删除'); return; }
    if (!confirm('确定要删除该语音角色？')) return;
    try {
      await apiFetch(`/api/voice/profiles/${profileId}`, { method: 'DELETE' });
      fetchProfiles();
    } catch (e: any) { setError(e.message); }
  };

  const handleTestTTS = async () => {
    if (!testText.trim()) return;
    try {
      setTesting(true);
      setTestResult(null);
      const body: any = { text: testText, save_output: false };
      if (testProfile) body.profile_id = testProfile;
      const data = await apiFetch('/api/voice/synthesize/json', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      setTestResult(data);
    } catch (e: any) {
      setTestResult({ error: e.message });
    } finally {
      setTesting(false);
    }
  };

  const handleSaveEdit = async () => {
    if (!editProfile) return;
    try {
      await apiFetch(`/api/voice/profiles/${editProfile.profile_id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: editProfile.name,
          description: editProfile.description,
          voice_id: editProfile.voice_id,
          rate: editProfile.rate,
          pitch: editProfile.pitch,
          volume: editProfile.volume,
        }),
      });
      setShowEdit(false);
      fetchProfiles();
    } catch (e: any) { setError(e.message); }
  };

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{profiles.length}</div>
          <div className="text-xs text-muted-foreground">语音角色</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-emerald-600">{profiles.filter((p) => p.builtin).length}</div>
          <div className="text-xs text-muted-foreground">内置角色</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-500">{voices.length}</div>
          <div className="text-xs text-muted-foreground">可用声音 (中文)</div>
        </div>
      </div>

      {/* TTS Test Card */}
      <div className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-center gap-2 mb-3">
          <Volume2 className="w-4 h-4 text-primary" />
          <h2 className="text-sm font-medium">TTS 测试</h2>
        </div>
        <div className="flex flex-wrap gap-2">
          <input
            className="flex-1 min-w-[200px] px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
            value={testText}
            onChange={(e) => setTestText(e.target.value)}
            placeholder="输入要合成的文字..."
          />
          <select
            className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
            value={testProfile}
            onChange={(e) => setTestProfile(e.target.value)}
          >
            <option value="">默认声音</option>
            {profiles.map((p) => <option key={p.profile_id} value={p.profile_id}>{p.name}</option>)}
          </select>
          <Button onClick={handleTestTTS}
            disabled={testing || !testText.trim()} variant="primary">
            {testing ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
            {testing ? '合成中...' : '测试'}
          </Button>
        </div>
        {testResult && !testResult.error && (
          <div className="mt-2 p-2 rounded bg-emerald-500/5 border border-emerald-500/20 text-xs">
            <CheckCircle className="w-3 h-3 inline text-emerald-600 mr-1" />
            引擎: {testResult.engine} | 声音: {testResult.voice_id} | 时长: {testResult.duration_seconds}s | 耗时: {testResult.elapsed_ms}ms
            {testResult.cached && ' (缓存)'}
          </div>
        )}
        {testResult?.error && (
          <div className="mt-2 p-2 rounded bg-red-500/5 border border-red-500/20 text-xs text-red-500">
            <AlertCircle className="w-3 h-3 inline mr-1" />{testResult.error}
          </div>
        )}
      </div>

      {/* Tab + Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <TabBar>
          <Tab active={tab === 'profiles'} onClick={() => setTab('profiles')}>语音角色</Tab>
          <Tab active={tab === 'voices'} onClick={() => setTab('voices')}>可用声音</Tab>
        </TabBar>
        <Button onClick={() => { fetchProfiles(); fetchVoices(); }} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
        {tab === 'profiles' && (
          <Button onClick={() => setShowCreate(true)} variant="primary" className="gap-1.5 ml-auto">
            <Plus className="w-3.5 h-3.5" />新建角色
          </Button>
        )}
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 p-2 text-xs text-red-500 bg-red-500/5 rounded-md border border-red-500/20">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <Button variant="bare" onClick={() => setError('')}><X className="w-3 h-3" /></Button>
        </div>
      )}

      {/* Profiles tab */}
      {tab === 'profiles' && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {profiles.map((p) => (
            <div key={p.profile_id} className="rounded-lg border border-border bg-card p-3 hover:bg-muted/30 transition-colors">
              <div className="flex items-start justify-between">
                <div className="min-w-0 flex-1 cursor-pointer" onClick={() => { setEditProfile(p); setShowEdit(true); }}>
                  <div className="flex items-center gap-2">
                    <Headphones className="w-4 h-4 text-primary shrink-0" />
                    <h3 className="text-xs font-medium truncate">{p.name}</h3>
                    {p.builtin && <Badge variant="default">内置</Badge>}
                  </div>
                  <p className="text-[10px] text-muted-foreground mt-0.5 line-clamp-1">{p.description || p.voice_id}</p>
                  <div className="flex items-center gap-2 mt-1.5">
                    <Badge variant="muted">{p.engine}</Badge>
                    <Badge variant="muted">{p.language}</Badge>
                    <span className="text-[10px] text-muted-foreground">使用: {p.usage_count}</span>
                  </div>
                  {p.tags.length > 0 && (
                    <div className="flex items-center gap-1 mt-1">
                      {p.tags.map((t) => <Badge key={t} variant="subtle">{t}</Badge>)}
                    </div>
                  )}
                </div>
                {!p.builtin && (
                  <Button onClick={() => handleDelete(p.profile_id, p.builtin)} variant="icon-sm" className="hover:bg-red-500/10" title="删除">
                    <Trash2 className="w-3.5 h-3.5 text-red-500" />
                  </Button>
                )}
              </div>
            </div>
          ))}
          {profiles.length === 0 && (
            <div className="col-span-full flex flex-col items-center justify-center py-12 text-muted-foreground">
              <Mic className="w-10 h-10 mb-2 opacity-30" />
              <p className="text-xs">暂无语音角色</p>
            </div>
          )}
        </div>
      )}

      {/* Voices tab */}
      {tab === 'voices' && (
        <div className="rounded-lg border border-border bg-card overflow-hidden">
          <table className="w-full text-xs lg:text-sm min-w-[500px]">
            <thead className="bg-muted/50">
              <tr>
                <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">ID</th>
                <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">名称</th>
                <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">语言</th>
                <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">性别</th>
              </tr>
            </thead>
            <tbody>
              {voices.map((v) => (
                <tr key={v.id} className="border-b border-border last:border-0 hover:bg-muted/30">
                  <td className="px-2 lg:px-4 py-2.5 text-xs font-mono">{v.id}</td>
                  <td className="px-2 lg:px-4 py-2.5 text-xs">{v.name}</td>
                  <td className="px-2 lg:px-4 py-2.5 text-xs">{v.language}</td>
                  <td className="px-2 lg:px-4 py-2.5 text-xs">{v.gender}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {voices.length === 0 && <div className="text-xs text-muted-foreground text-center py-8">暂无可用声音</div>}
        </div>
      )}

      {/* Create Dialog */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowCreate(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4 max-h-[80vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">新建语音角色</h2>
              <Button onClick={() => setShowCreate(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">名称 *</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={createForm.name} onChange={(e) => setCreateForm((f) => ({ ...f, name: e.target.value }))} placeholder="我的语音角色" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground">引擎</label>
                  <select className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={createForm.engine} onChange={(e) => setCreateForm((f) => ({ ...f, engine: e.target.value }))}>
                    <option value="edge-tts">Edge TTS</option>
                    <option value="piper">Piper</option>
                    <option value="openai">OpenAI</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">语言</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={createForm.language} onChange={(e) => setCreateForm((f) => ({ ...f, language: e.target.value }))} />
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">声音ID</label>
                <select className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={createForm.voice_id} onChange={(e) => setCreateForm((f) => ({ ...f, voice_id: e.target.value }))}>
                  {voices.map((v) => <option key={v.id} value={v.id}>{v.name} ({v.gender})</option>)}
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">描述</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={createForm.description} onChange={(e) => setCreateForm((f) => ({ ...f, description: e.target.value }))} />
              </div>
              <div className="grid grid-cols-3 gap-3">
                <div><label className="text-xs text-muted-foreground">语速</label><input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={createForm.rate} onChange={(e) => setCreateForm((f) => ({ ...f, rate: e.target.value }))} /></div>
                <div><label className="text-xs text-muted-foreground">音调</label><input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={createForm.pitch} onChange={(e) => setCreateForm((f) => ({ ...f, pitch: e.target.value }))} /></div>
                <div><label className="text-xs text-muted-foreground">音量</label><input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={createForm.volume} onChange={(e) => setCreateForm((f) => ({ ...f, volume: e.target.value }))} /></div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">标签（逗号分隔）</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={createForm.tags} onChange={(e) => setCreateForm((f) => ({ ...f, tags: e.target.value }))} placeholder="温柔, 女声" />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowCreate(false)} variant="secondary">取消</Button>
                <Button onClick={handleCreate} disabled={!createForm.name || creating} variant="primary">
                  {creating ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}创建
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Edit Dialog */}
      {showEdit && editProfile && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowEdit(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">编辑角色 — {editProfile.name}</h2>
              <Button onClick={() => setShowEdit(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div><label className="text-xs text-muted-foreground">名称</label><input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={editProfile.name} onChange={(e) => setEditProfile((p) => p ? { ...p, name: e.target.value } : null)} /></div>
              <div><label className="text-xs text-muted-foreground">声音ID</label><input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={editProfile.voice_id} onChange={(e) => setEditProfile((p) => p ? { ...p, voice_id: e.target.value } : null)} /></div>
              <div className="grid grid-cols-3 gap-3">
                <div><label className="text-xs text-muted-foreground">语速</label><input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={editProfile.rate} onChange={(e) => setEditProfile((p) => p ? { ...p, rate: e.target.value } : null)} /></div>
                <div><label className="text-xs text-muted-foreground">音调</label><input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={editProfile.pitch} onChange={(e) => setEditProfile((p) => p ? { ...p, pitch: e.target.value } : null)} /></div>
                <div><label className="text-xs text-muted-foreground">音量</label><input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={editProfile.volume} onChange={(e) => setEditProfile((p) => p ? { ...p, volume: e.target.value } : null)} /></div>
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowEdit(false)} variant="secondary">取消</Button>
                <Button onClick={handleSaveEdit} variant="primary">
                  <Save className="w-3 h-3" />保存
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
