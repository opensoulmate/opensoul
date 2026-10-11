'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import { Search, BookOpen, Plus, Trash2, X, RefreshCw, Edit2, Save, Star, Pin, Upload, Eye, Filter } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

interface KnowledgeEntry {
  id: string;
  title: string;
  content: string;
  content_type: string;
  source: string;
  tags: string[];
  starred: boolean;
  pinned: boolean;
  created_at: string;
  updated_at: string;
}

export default function KnowledgePage() {
  const [entries, setEntries] = useState<KnowledgeEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [filterType, setFilterType] = useState('all');
  const [filterStarred, setFilterStarred] = useState(false);
  const [types, setTypes] = useState<string[]>([]);

  // Create dialog
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState({ title: '', content: '', content_type: 'text', source: 'manual', tags: '' });
  const [creating, setCreating] = useState(false);

  // Detail dialog
  const [selected, setSelected] = useState<KnowledgeEntry | null>(null);
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState({ title: '', content: '', content_type: '', source: '', tags: '' });

  // Upload
  const [uploading, setUploading] = useState(false);

  const fetchEntries = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiFetch('/api/knowledge/?limit=200');
      const list = Array.isArray(data) ? data : data.entries || [];
      setEntries(list);
      const t = new Set<string>();
      list.forEach((e: KnowledgeEntry) => e.content_type && t.add(e.content_type));
      setTypes(Array.from(t).sort());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchEntries(); }, [fetchEntries]);

  const handleCreate = async () => {
    if (!createForm.title || !createForm.content) return;
    setCreating(true);
    try {
      await apiFetch('/api/knowledge/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...createForm,
          tags: createForm.tags ? createForm.tags.split(',').map((t) => t.trim()) : [],
        }),
      });
      setShowCreate(false);
      setCreateForm({ title: '', content: '', content_type: 'text', source: 'manual', tags: '' });
      await fetchEntries();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('确定删除此知识条目？')) return;
    try {
      await apiFetch(`/api/knowledge/${id}`, { method: 'DELETE' });
      setEntries((prev) => prev.filter((e) => e.id !== id));
      if (selected?.id === id) setSelected(null);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleStar = async (id: string) => {
    try {
      await apiFetch(`/api/knowledge/${id}/star`, { method: 'POST' });
      setEntries((prev) => prev.map((e) => e.id === id ? { ...e, starred: !e.starred } : e));
      if (selected?.id === id) setSelected((s) => s ? { ...s, starred: !s.starred } : null);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handlePin = async (id: string) => {
    try {
      await apiFetch(`/api/knowledge/${id}/pin`, { method: 'POST' });
      setEntries((prev) => prev.map((e) => e.id === id ? { ...e, pinned: !e.pinned } : e));
      if (selected?.id === id) setSelected((s) => s ? { ...s, pinned: !s.pinned } : null);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const startEdit = (entry: KnowledgeEntry) => {
    setEditForm({
      title: entry.title,
      content: entry.content,
      content_type: entry.content_type,
      source: entry.source,
      tags: entry.tags?.join(', ') || '',
    });
    setEditing(true);
  };

  const handleSave = async () => {
    if (!selected) return;
    try {
      await apiFetch(`/api/knowledge/${selected.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...editForm,
          tags: editForm.tags ? editForm.tags.split(',').map((t) => t.trim()) : [],
        }),
      });
      setEditing(false);
      await fetchEntries();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const token = localStorage.getItem('soul_token');
      const res = await fetch('/api/knowledge/upload', {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: formData,
      });
      if (!res.ok) throw new Error(`上传失败: ${res.status}`);
      await fetchEntries();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setUploading(false);
      e.target.value = '';
    }
  };

  const filtered = entries.filter((e) => {
    if (filterType !== 'all' && e.content_type !== filterType) return false;
    if (filterStarred && !e.starred) return false;
    if (search) {
      const q = search.toLowerCase();
      return (
        e.title.toLowerCase().includes(q) ||
        e.content.toLowerCase().includes(q) ||
        e.source.toLowerCase().includes(q) ||
        e.tags?.some((t) => t.toLowerCase().includes(q))
      );
    }
    return true;
  });

  const pinnedEntries = filtered.filter((e) => e.pinned);
  const normalEntries = filtered.filter((e) => !e.pinned);

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 dark:bg-red-950/20 dark:border-red-800 p-3 text-xs text-red-600 dark:text-red-400 flex items-center justify-between">
          <span>{error}</span>
          <Button onClick={() => setError('')} variant="icon-xs" className="hover:bg-red-100 dark:hover:bg-red-900/30"><X className="w-3.5 h-3.5" /></Button>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{entries.length}</div>
          <div className="text-xs text-muted-foreground">总条目</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-yellow-500">{entries.filter((e) => e.starred).length}</div>
          <div className="text-xs text-muted-foreground">已收藏</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-600">{entries.filter((e) => e.pinned).length}</div>
          <div className="text-xs text-muted-foreground">已置顶</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-600">{types.length}</div>
          <div className="text-xs text-muted-foreground">内容类型</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索标题、内容、标签..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={filterType}
          onChange={(e) => setFilterType(e.target.value)}
        >
          <option value="all">全部类型</option>
          {types.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <Button
          onClick={() => setFilterStarred(!filterStarred)}
          variant="chip-sm" className={`flex items-center gap-1 border  ${
            filterStarred ? 'bg-yellow-50 border-yellow-300 text-yellow-700 dark:bg-yellow-950/20 dark:border-yellow-800' : 'bg-muted border-border hover:bg-muted'
          }`}
        >
          <Star className="w-3.5 h-3.5" />收藏
        </Button>
        <label className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-muted border border-border rounded-md hover:bg-muted/80 cursor-pointer">
          <Upload className="w-3.5 h-3.5" />{uploading ? '上传中...' : '上传文件'}
          <input type="file" className="hidden" onChange={handleUpload} disabled={uploading} />
        </label>
        <Button onClick={fetchEntries} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
        <Button onClick={() => setShowCreate(true)} variant="primary" className="gap-1.5">
          <Plus className="w-3.5 h-3.5" />添加条目
        </Button>
      </div>

      {/* Knowledge List */}
      <div className="space-y-2">
        {/* Pinned */}
        {pinnedEntries.map((entry) => (
          <KnowledgeCard
            key={entry.id}
            entry={entry}
            onSelect={setSelected}
            onStar={handleStar}
            onPin={handlePin}
            onDelete={handleDelete}
            pinned
          />
        ))}
        {/* Normal */}
        {normalEntries.map((entry) => (
          <KnowledgeCard
            key={entry.id}
            entry={entry}
            onSelect={setSelected}
            onStar={handleStar}
            onPin={handlePin}
            onDelete={handleDelete}
          />
        ))}
        {filtered.length === 0 && (
          <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
            <BookOpen className="w-10 h-10 mb-2 opacity-30" />
            <p className="text-xs">暂无知识条目</p>
          </div>
        )}
      </div>

      {/* Detail / Edit Dialog */}
      {selected && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => { setSelected(null); setEditing(false); }}>
          <div className="bg-card rounded-lg border border-border w-full max-w-2xl mx-4 max-h-[80vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium truncate flex-1 mr-2">{editing ? '编辑条目' : selected.title}</h2>
              <div className="flex items-center gap-1.5">
                {!editing && (
                  <Button onClick={() => startEdit(selected)} variant="icon-md" className="hover:bg-muted" title="编辑">
                    <Edit2 className="w-4 h-4" />
                  </Button>
                )}
                <Button onClick={() => { setSelected(null); setEditing(false); }} variant="icon-md" className="hover:bg-muted">
                  <X className="w-4 h-4" />
                </Button>
              </div>
            </div>
            <div className="p-4 space-y-3">
              {editing ? (
                <>
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">标题</label>
                    <input
                      className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                      value={editForm.title}
                      onChange={(e) => setEditForm((f) => ({ ...f, title: e.target.value }))}
                    />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">内容</label>
                    <textarea
                      className="w-full h-40 px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 resize-y"
                      value={editForm.content}
                      onChange={(e) => setEditForm((f) => ({ ...f, content: e.target.value }))}
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="text-xs text-muted-foreground mb-1 block">类型</label>
                      <input
                        className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                        value={editForm.content_type}
                        onChange={(e) => setEditForm((f) => ({ ...f, content_type: e.target.value }))}
                      />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground mb-1 block">来源</label>
                      <input
                        className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                        value={editForm.source}
                        onChange={(e) => setEditForm((f) => ({ ...f, source: e.target.value }))}
                      />
                    </div>
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">标签（逗号分隔）</label>
                    <input
                      className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                      value={editForm.tags}
                      onChange={(e) => setEditForm((f) => ({ ...f, tags: e.target.value }))}
                    />
                  </div>
                  <div className="flex justify-end gap-2 pt-2">
                    <Button onClick={() => setEditing(false)} variant="secondary">取消</Button>
                    <Button onClick={handleSave} variant="primary">
                      <Save className="w-3.5 h-3.5" />保存
                    </Button>
                  </div>
                </>
              ) : (
                <>
                  <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                    <span className="px-2 py-0.5 rounded bg-muted">{selected.content_type}</span>
                    <span>来源: {selected.source}</span>
                    <span>{selected.created_at}</span>
                  </div>
                  <div className="text-sm whitespace-pre-wrap bg-muted/50 rounded-lg p-3 max-h-[400px] overflow-auto">
                    {selected.content}
                  </div>
                  {selected.tags?.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {selected.tags.map((t) => (
                        <Badge variant="default" key={t}>#{t}</Badge>
                      ))}
                    </div>
                  )}
                  <div className="flex items-center gap-2 pt-2 border-t border-border">
                    <Button
                      onClick={() => handleStar(selected.id)}
                      variant="chip-xs" className={`flex items-center gap-1  ${selected.starred ? 'bg-yellow-100 text-yellow-700 dark:bg-yellow-950/20' : 'bg-muted text-muted-foreground hover:bg-muted'}`}
                    >
                      <Star className="w-3 h-3" />{selected.starred ? '已收藏' : '收藏'}
                    </Button>
                    <Button
                      onClick={() => handlePin(selected.id)}
                      variant="chip-xs" className={`flex items-center gap-1  ${selected.pinned ? 'bg-blue-100 text-blue-700 dark:bg-blue-950/20' : 'bg-muted text-muted-foreground hover:bg-muted'}`}
                    >
                      <Pin className="w-3 h-3" />{selected.pinned ? '已置顶' : '置顶'}
                    </Button>
                    <Button
                      onClick={() => handleDelete(selected.id)}
                      variant="chip-xs" className="flex items-center gap-1 bg-red-50 text-red-500 hover:bg-red-100 dark:bg-red-950/20 dark:hover:bg-red-950/30"
                    >
                      <Trash2 className="w-3 h-3" />删除
                    </Button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Create Dialog */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowCreate(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4 max-h-[80vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">添加知识条目</h2>
              <Button onClick={() => setShowCreate(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">标题 *</label>
                <input
                  className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                  placeholder="输入标题..."
                  value={createForm.title}
                  onChange={(e) => setCreateForm((f) => ({ ...f, title: e.target.value }))}
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">内容 *</label>
                <textarea
                  className="w-full h-32 px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 resize-y"
                  placeholder="输入内容..."
                  value={createForm.content}
                  onChange={(e) => setCreateForm((f) => ({ ...f, content: e.target.value }))}
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">类型</label>
                  <input
                    className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                    value={createForm.content_type}
                    onChange={(e) => setCreateForm((f) => ({ ...f, content_type: e.target.value }))}
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">来源</label>
                  <input
                    className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                    value={createForm.source}
                    onChange={(e) => setCreateForm((f) => ({ ...f, source: e.target.value }))}
                  />
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">标签（逗号分隔）</label>
                <input
                  className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                  placeholder="tag1, tag2, tag3..."
                  value={createForm.tags}
                  onChange={(e) => setCreateForm((f) => ({ ...f, tags: e.target.value }))}
                />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowCreate(false)} variant="secondary">取消</Button>
                <Button onClick={handleCreate}
                  disabled={creating} variant="primary">
                  {creating ? '创建中...' : '创建'}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// Knowledge Card Component
function KnowledgeCard({
  entry, onSelect, onStar, onPin, onDelete, pinned: isPinned,
}: {
  entry: KnowledgeEntry;
  onSelect: (e: KnowledgeEntry) => void;
  onStar: (id: string) => void;
  onPin: (id: string) => void;
  onDelete: (id: string) => void;
  pinned?: boolean;
}) {
  return (
    <div
      className={`rounded-lg border bg-card p-4 hover:bg-muted/30 transition-colors cursor-pointer ${
        isPinned ? 'border-blue-200 dark:border-blue-900' : 'border-border'
      }`}
      onClick={() => onSelect(entry)}
    >
      <div className="flex items-start justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            {isPinned && <Pin className="w-3 h-3 text-blue-500 shrink-0" />}
            <h3 className="text-sm font-medium truncate">{entry.title}</h3>
          </div>
          <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{entry.content}</p>
        </div>
        <div className="flex items-center gap-1 shrink-0 ml-2" onClick={(e) => e.stopPropagation()}>
          <Badge variant="muted">{entry.content_type}</Badge>
        </div>
      </div>
      <div className="flex items-center justify-between mt-2">
        <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
          <span>{entry.source}</span>
          <span>{entry.created_at}</span>
          {entry.tags?.slice(0, 3).map((t) => (
            <span key={t} className="px-1.5 py-0.5 rounded bg-primary/5 text-primary/70">#{t}</span>
          ))}
        </div>
        <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
          <Button
            onClick={() => onStar(entry.id)}
            variant="icon-sm" className={` ${entry.starred ? 'text-yellow-500' : 'text-muted-foreground hover:text-yellow-500'}`}
            title="收藏"
          >
            <Star className="w-3.5 h-3.5" fill={entry.starred ? 'currentColor' : 'none'} />
          </Button>
          <Button
            onClick={() => onPin(entry.id)}
            variant="icon-sm" className={` ${entry.pinned ? 'text-blue-500' : 'text-muted-foreground hover:text-blue-500'}`}
            title="置顶"
          >
            <Pin className="w-3.5 h-3.5" />
          </Button>
          <Button
            onClick={() => onDelete(entry.id)}
            variant="icon-sm" className="text-muted-foreground hover:text-red-500"
            title="删除"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </Button>
        </div>
      </div>
    </div>
  );
}
