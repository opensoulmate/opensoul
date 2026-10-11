'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Plus, Trash2, RefreshCw, X, Database, Edit3, Save, Zap,
  Tag, BarChart3, Layers, Settings
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

interface Entity {
  id: string;
  name: string;
  entity_type: string;
  description: string;
  properties: Record<string, any>;
  user_id: string;
  created_at: string;
  updated_at: string;
}

type Tab = 'entities' | 'tags';

interface TagItem {
  id: string;
  name: string;
  color: string;
  usage_count: number;
  created_at: string;
}

const ENTITY_TYPES = ['person', 'place', 'concept', 'event', 'document', 'project', 'tool', 'organization', 'other'];
const PRESET_COLORS = ['#ef4444', '#f97316', '#eab308', '#22c55e', '#3b82f6', '#8b5cf6', '#ec4899', '#6b7280'];

export default function EntityPage() {
  const [tab, setTab] = useState<Tab>('entities');
  const [entities, setEntities] = useState<Entity[]>([]);
  const [tags, setTags] = useState<TagItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [filterType, setFilterType] = useState('');
  const [stats, setStats] = useState<any>(null);

  // Entity CRUD
  const [showEntityDialog, setShowEntityDialog] = useState(false);
  const [editEntityId, setEditEntityId] = useState<string | null>(null);
  const [entityForm, setEntityForm] = useState({ name: '', entity_type: 'concept', description: '', properties: '' });
  const [saving, setSaving] = useState(false);
  const [selectedEntity, setSelectedEntity] = useState<Entity | null>(null);
  const [showEntityDetail, setShowEntityDetail] = useState(false);

  // Tag CRUD
  const [showTagDialog, setShowTagDialog] = useState(false);
  const [editTagId, setEditTagId] = useState<string | null>(null);
  const [tagForm, setTagForm] = useState({ name: '', color: '#3b82f6' });

  const fetchEntities = useCallback(async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams({ limit: '100' });
      if (filterType) params.set('entity_type', filterType);
      const data = await apiFetch(`/api/entity/?${params}`);
      setEntities(Array.isArray(data) ? data : []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [filterType]);

  const fetchTags = useCallback(async () => {
    try {
      const data = await apiFetch('/api/tags/?user_id=00000000-0000-0000-0000-000000000000');
      setTags(Array.isArray(data) ? data : []);
    } catch { /* silent */ }
  }, []);

  const fetchStats = useCallback(async () => {
    try {
      const data = await apiFetch('/api/entity/stats');
      setStats(data);
    } catch { /* silent */ }
  }, []);

  useEffect(() => { fetchEntities(); fetchTags(); fetchStats(); }, [fetchEntities, fetchTags, fetchStats]);

  // Entity handlers
  const handleSaveEntity = async () => {
    try {
      setSaving(true);
      const body: any = {
        name: entityForm.name,
        entity_type: entityForm.entity_type,
        description: entityForm.description,
      };
      try { body.properties = entityForm.properties ? JSON.parse(entityForm.properties) : {}; } catch { body.properties = {}; }
      if (editEntityId) {
        await apiFetch(`/api/entity/${editEntityId}?user_id=default`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
      } else {
        await apiFetch('/api/entity/?user_id=default', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
      }
      setShowEntityDialog(false);
      await fetchEntities();
      await fetchStats();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteEntity = async (id: string) => {
    if (!confirm('确定删除此实体？')) return;
    try {
      await apiFetch(`/api/entity/${id}?user_id=default`, { method: 'DELETE' });
      await fetchEntities();
      await fetchStats();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const openEditEntity = async (id: string) => {
    try {
      const data = await apiFetch(`/api/entity/${id}?user_id=default`);
      setEditEntityId(id);
      setEntityForm({
        name: data.name || '',
        entity_type: data.entity_type || 'concept',
        description: data.description || '',
        properties: data.properties ? JSON.stringify(data.properties, null, 2) : '',
      });
      setShowEntityDialog(true);
    } catch (e: any) {
      setError(e.message);
    }
  };

  // Tag handlers
  const handleSaveTag = async () => {
    try {
      setSaving(true);
      if (editTagId) {
        await apiFetch(`/api/tags/${editTagId}?user_id=00000000-0000-0000-0000-000000000000`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: tagForm.name, color: tagForm.color }),
        });
      } else {
        await apiFetch('/api/tags/?user_id=00000000-0000-0000-0000-000000000000', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: tagForm.name, color: tagForm.color }),
        });
      }
      setShowTagDialog(false);
      await fetchTags();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteTag = async (id: string) => {
    if (!confirm('确定删除此标签？')) return;
    try {
      await apiFetch(`/api/tags/${id}?user_id=00000000-0000-0000-0000-000000000000`, { method: 'DELETE' });
      await fetchTags();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const filteredEntities = entities.filter(e => {
    if (!search) return true;
    const q = search.toLowerCase();
    return e.name.toLowerCase().includes(q) || e.description.toLowerCase().includes(q) || e.entity_type.toLowerCase().includes(q);
  });

  const typeColor = (t: string) => {
    const map: Record<string, string> = {
      person: 'bg-blue-500/10 text-blue-500',
      place: 'bg-emerald-500/10 text-emerald-500',
      concept: 'bg-violet-500/10 text-violet-500',
      event: 'bg-amber-500/10 text-amber-500',
      document: 'bg-gray-500/10 text-gray-500',
      project: 'bg-pink-500/10 text-pink-500',
      tool: 'bg-teal-500/10 text-teal-500',
      organization: 'bg-amber-500/10 text-amber-500',
    };
    return map[t] || 'bg-muted text-muted-foreground';
  };

  const tabs: { key: Tab; label: string; icon: any }[] = [
    { key: 'entities', label: '实体', icon: Database },
    { key: 'tags', label: '标签', icon: Tag },
  ];

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-500/10 text-red-500 text-xs">
          {error}
          <Button variant="bare" onClick={() => setError('')} className="ml-auto"><X className="w-3.5 h-3.5" /></Button>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{stats?.total_entities ?? entities.length}</div>
          <div className="text-xs text-muted-foreground">实体总数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-500">{Object.keys(stats?.by_type || {}).length}</div>
          <div className="text-xs text-muted-foreground">实体类型</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-600">{tags.length}</div>
          <div className="text-xs text-muted-foreground">标签数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-purple-500">{tags.reduce((s, t) => s + t.usage_count, 0)}</div>
          <div className="text-xs text-muted-foreground">标签使用次数</div>
        </div>
      </div>

      {/* Tab Bar */}
      <div className="flex items-center gap-1 p-1 bg-muted rounded-lg w-fit">
        {tabs.map(t => (
          <Button variant="bare" key={t.key} onClick={() => setTab(t.key)}
            className={`flex items-center gap-1.5 px-3 py-2 text-xs lg:text-sm rounded-lg transition-colors ${tab === t.key ? 'bg-primary/10 text-primary font-medium' : 'text-muted-foreground hover:text-foreground'}`}>
            <t.icon className="w-3.5 h-3.5" />{t.label}
          </Button>
        ))}
      </div>

      {/* Entities Tab */}
      {tab === 'entities' && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative flex-1 min-w-[200px]">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
              <input className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" placeholder="搜索实体名称、描述..." value={search} onChange={e => setSearch(e.target.value)} />
            </div>
            <select className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={filterType} onChange={e => setFilterType(e.target.value)}>
              <option value="">全部类型</option>
              {ENTITY_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
            <Button onClick={() => { setEditEntityId(null); setEntityForm({ name: '', entity_type: 'concept', description: '', properties: '' }); setShowEntityDialog(true); }} variant="chip-sm" className="flex items-center gap-1.5 bg-primary/10 text-primary border border-border hover:bg-primary/20">
              <Plus className="w-3.5 h-3.5" />新建实体
            </Button>
            <Button onClick={fetchEntities} variant="secondary">
              <RefreshCw className="w-3.5 h-3.5" />刷新
            </Button>
          </div>

          {/* Type distribution */}
          {stats?.by_type && Object.keys(stats.by_type).length > 0 && (
            <div className="flex flex-wrap gap-2">
              {Object.entries(stats.by_type).sort((a, b) => (b[1] as number) - (a[1] as number)).map(([type, cnt]) => (
                <Button variant="bare" key={type} onClick={() => setFilterType(filterType === type ? '' : type)}
                  className={`flex items-center gap-1.5 px-2 py-1 text-xs lg:text-sm rounded-lg border border-border transition-colors ${filterType === type ? 'bg-primary/10 text-primary' : 'bg-card hover:bg-muted/50'}`}>
                  <Badge className={`${typeColor(type)}`}>{type}</Badge>
                  <span className="font-medium">{String(cnt)}</span>
                </Button>
              ))}
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {filteredEntities.map(e => (
              <div key={e.id} className="rounded-lg border border-border bg-card p-4 hover:bg-muted/30 transition-colors cursor-pointer" onClick={() => { setSelectedEntity(e); setShowEntityDetail(true); }}>
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-medium">{e.name}</h3>
                    <Badge variant="bare" className={`${typeColor(e.entity_type)}`}>{e.entity_type}</Badge>
                  </div>
                  <div className="flex items-center gap-1" onClick={ev => ev.stopPropagation()}>
                    <Button onClick={() => openEditEntity(e.id)} variant="icon-sm" className="text-muted-foreground hover:text-foreground"><Edit3 className="w-3.5 h-3.5" /></Button>
                    <Button onClick={() => handleDeleteEntity(e.id)} variant="icon-sm" className="text-muted-foreground hover:text-red-500"><Trash2 className="w-3.5 h-3.5" /></Button>
                  </div>
                </div>
                <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{e.description || '暂无描述'}</p>
                {e.properties && Object.keys(e.properties).length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-2">
                    {Object.entries(e.properties).slice(0, 3).map(([k, v]) => (
                      <Badge variant="muted" key={k} className="text-inherit">{k}: {String(v).slice(0, 20)}</Badge>
                    ))}
                  </div>
                )}
                <div className="text-[10px] text-muted-foreground mt-2">{new Date(e.created_at).toLocaleDateString()}</div>
              </div>
            ))}
            {filteredEntities.length === 0 && (
              <div className="col-span-full text-center text-sm text-muted-foreground py-8">暂无实体数据</div>
            )}
          </div>
        </>
      )}

      {/* Tags Tab */}
      {tab === 'tags' && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={() => { setEditTagId(null); setTagForm({ name: '', color: '#3b82f6' }); setShowTagDialog(true); }} variant="chip-sm" className="flex items-center gap-1.5 bg-primary/10 text-primary border border-border hover:bg-primary/20">
              <Plus className="w-3.5 h-3.5" />新建标签
            </Button>
            <Button onClick={fetchTags} variant="secondary">
              <RefreshCw className="w-3.5 h-3.5" />刷新
            </Button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {tags.map(t => (
              <div key={t.id} className="rounded-lg border border-border bg-card p-4 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="w-4 h-4 rounded-full" style={{ backgroundColor: t.color }} />
                  <div>
                    <div className="text-sm font-medium">{t.name}</div>
                    <div className="text-xs text-muted-foreground">使用 {t.usage_count} 次</div>
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <Button onClick={() => { setEditTagId(t.id); setTagForm({ name: t.name, color: t.color }); setShowTagDialog(true); }} variant="icon-sm" className="text-muted-foreground hover:text-foreground"><Edit3 className="w-3.5 h-3.5" /></Button>
                  <Button onClick={() => handleDeleteTag(t.id)} variant="icon-sm" className="text-muted-foreground hover:text-red-500"><Trash2 className="w-3.5 h-3.5" /></Button>
                </div>
              </div>
            ))}
            {tags.length === 0 && (
              <div className="col-span-full text-center text-sm text-muted-foreground py-8">暂无标签</div>
            )}
          </div>
        </>
      )}

      {/* Entity Detail Dialog */}
      {showEntityDetail && selectedEntity && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowEntityDetail(false)}>
          <div className="bg-card rounded-lg border border-border p-6 max-w-lg w-full mx-4 max-h-[80vh] overflow-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-medium">{selectedEntity.name}</h2>
              <Button variant="bare" onClick={() => setShowEntityDetail(false)}><X className="w-4 h-4 text-muted-foreground" /></Button>
            </div>
            <div className="space-y-3 text-sm">
              <div className="flex items-center gap-2"><Badge className={`${typeColor(selectedEntity.entity_type)}`}>{selectedEntity.entity_type}</Badge></div>
              <div><span className="text-muted-foreground">描述: </span>{selectedEntity.description || '暂无'}</div>
              <div><span className="text-muted-foreground">ID: </span><code className="text-xs bg-muted px-1.5 py-0.5 rounded">{selectedEntity.id}</code></div>
              {selectedEntity.properties && Object.keys(selectedEntity.properties).length > 0 && (
                <div>
                  <span className="text-muted-foreground">属性:</span>
                  <pre className="text-xs bg-muted p-2 rounded mt-1 overflow-auto">{JSON.stringify(selectedEntity.properties, null, 2)}</pre>
                </div>
              )}
              <div className="text-xs text-muted-foreground">
                <div>创建: {new Date(selectedEntity.created_at).toLocaleString()}</div>
                <div>更新: {new Date(selectedEntity.updated_at).toLocaleString()}</div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Entity Create/Edit Dialog */}
      {showEntityDialog && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowEntityDialog(false)}>
          <div className="bg-card rounded-lg border border-border p-6 max-w-md w-full mx-4" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-medium">{editEntityId ? '编辑实体' : '新建实体'}</h2>
              <Button variant="bare" onClick={() => setShowEntityDialog(false)}><X className="w-4 h-4 text-muted-foreground" /></Button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">名称 *</label>
                <input className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={entityForm.name} onChange={e => setEntityForm({ ...entityForm, name: e.target.value })} placeholder="实体名称" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">类型</label>
                <select className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md" value={entityForm.entity_type} onChange={e => setEntityForm({ ...entityForm, entity_type: e.target.value })}>
                  {ENTITY_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">描述</label>
                <textarea className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 h-20 resize-none" value={entityForm.description} onChange={e => setEntityForm({ ...entityForm, description: e.target.value })} placeholder="描述信息" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">属性（JSON）</label>
                <textarea className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 h-20 resize-none font-mono" value={entityForm.properties} onChange={e => setEntityForm({ ...entityForm, properties: e.target.value })} placeholder='{"key": "value"}' />
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-6">
              <Button onClick={() => setShowEntityDialog(false)} variant="chip-sm" className="bg-muted border border-border">取消</Button>
              <Button onClick={handleSaveEntity} disabled={saving || !entityForm.name.trim()} variant="primary" className="gap-1.5">
                {saving ? <Zap className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}保存
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Tag Create/Edit Dialog */}
      {showTagDialog && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowTagDialog(false)}>
          <div className="bg-card rounded-lg border border-border p-6 max-w-sm w-full mx-4" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-medium">{editTagId ? '编辑标签' : '新建标签'}</h2>
              <Button variant="bare" onClick={() => setShowTagDialog(false)}><X className="w-4 h-4 text-muted-foreground" /></Button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">名称 *</label>
                <input className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={tagForm.name} onChange={e => setTagForm({ ...tagForm, name: e.target.value })} placeholder="标签名称" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">颜色</label>
                <div className="flex items-center gap-2">
                  {PRESET_COLORS.map(c => (
                    <Button variant="bare" key={c} onClick={() => setTagForm({ ...tagForm, color: c })}
                      className={`w-6 h-6 rounded-full border-2 transition-colors ${tagForm.color === c ? 'border-foreground' : 'border-transparent'}`}
                      style={{ backgroundColor: c }} />
                  ))}
                  <input type="color" value={tagForm.color} onChange={e => setTagForm({ ...tagForm, color: e.target.value })} className="w-6 h-6 cursor-pointer" />
                </div>
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-6">
              <Button onClick={() => setShowTagDialog(false)} variant="chip-sm" className="bg-muted border border-border">取消</Button>
              <Button onClick={handleSaveTag} disabled={saving || !tagForm.name.trim()} variant="primary" className="gap-1.5">
                {saving ? <Zap className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}保存
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
