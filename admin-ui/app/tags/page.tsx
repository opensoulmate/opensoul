'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, RefreshCw, X, Tag, Plus, Trash2, Edit3, Save,
  AlertTriangle, Loader2, CheckCircle, Palette, Hash,
} from 'lucide-react';
import { Button } from '@/components/ui/button';

// ── Types ───────────────────────────────────────────────────

interface TagItem {
  id: string;
  name: string;
  color: string;
  user_id: string;
  usage_count: number;
  created_at: string;
}

// ── Helpers ─────────────────────────────────────────────────

const PRESET_COLORS = [
  '#ef4444', '#f97316', '#f59e0b', '#eab308', '#84cc16',
  '#22c55e', '#10b981', '#14b8a6', '#06b6d4', '#0ea5e9',
  '#3b82f6', '#6366f1', '#8b5cf6', '#a855f7', '#d946ef',
  '#ec4899', '#f43f5e', '#78716c', '#64748b', '#6b7280',
];

function formatTime(ts: string) {
  if (!ts) return '-';
  try {
    return new Date(ts).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    });
  } catch { return ts; }
}

// ── Tag Edit Dialog ─────────────────────────────────────────

function TagDialog({ tag, onClose, onSave }: {
  tag?: TagItem;
  onClose: () => void;
  onSave: (name: string, color: string) => void;
}) {
  const [name, setName] = useState(tag?.name || '');
  const [color, setColor] = useState(tag?.color || PRESET_COLORS[0]);
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    if (!name.trim()) return;
    setSaving(true);
    try {
      if (tag) {
        // Update
        await apiFetch(`/api/tags/${tag.id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, color }),
        });
      } else {
        // Create
        await apiFetch('/api/tags/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, color }),
        });
      }
      onSave(name, color);
    } catch (e: any) {
      // ignore
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div className="w-full max-w-md rounded-xl border border-border bg-card p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 mb-4">
          <Tag className="w-4 h-4" />
          <span className="text-sm font-medium">{tag ? '编辑标签' : '新建标签'}</span>
          <button onClick={onClose} className="ml-auto p-1 hover:bg-muted rounded-md"><X className="w-4 h-4" /></button>
        </div>

        <div className="space-y-3">
          <div>
            <label className="text-xs text-muted-foreground mb-1 block">标签名称</label>
            <input
              className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
              placeholder="输入标签名称"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
            />
          </div>

          <div>
            <label className="text-xs text-muted-foreground mb-1 block">颜色</label>
            <div className="flex flex-wrap gap-1.5">
              {PRESET_COLORS.map((c) => (
                <button
                  key={c}
                  onClick={() => setColor(c)}
                  className={`w-6 h-6 rounded-full border-2 transition-all ${color === c ? 'border-foreground scale-110' : 'border-transparent'}`}
                  style={{ backgroundColor: c }}
                />
              ))}
            </div>
            <div className="mt-2 flex items-center gap-2">
              <span className="text-[10px] text-muted-foreground">预览:</span>
              <span
                className="rounded-full px-2 py-0.5 text-xs font-medium text-white font-medium"
                style={{ backgroundColor: color }}
              >
                {name || '标签名'}
              </span>
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-2 mt-4">
          <Button onClick={onClose} variant="secondary">取消</Button>
          <Button onClick={handleSave}
            disabled={saving || !name.trim()} variant="primary" className="gap-1.5">
            {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
            {tag ? '保存' : '创建'}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────

export default function TagsPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [tags, setTags] = useState<TagItem[]>([]);
  const [search, setSearch] = useState('');
  const [editingTag, setEditingTag] = useState<TagItem | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);

  const fetchTags = useCallback(async () => {
    try {
      setLoading(true);
      setError('');
      const data = await apiFetch('/api/tags/');
      setTags(Array.isArray(data) ? data : []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchTags(); }, [fetchTags]);

  const handleDelete = async (id: string) => {
    setDeleting(id);
    try {
      await apiFetch(`/api/tags/${id}`, { method: 'DELETE' });
      setTags((prev) => prev.filter((t) => t.id !== id));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setDeleting(null);
    }
  };

  // Filter
  const filtered = tags.filter((t) => {
    if (search) {
      return t.name.toLowerCase().includes(search.toLowerCase());
    }
    return true;
  });

  // Stats
  const totalUsage = tags.reduce((s, t) => s + (t.usage_count || 0), 0);
  const topTag = tags.length > 0 ? tags.reduce((a, b) => (a.usage_count || 0) > (b.usage_count || 0) ? a : b) : null;

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* ── Stats ── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{tags.length}</div>
          <div className="text-xs text-muted-foreground">标签总数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-primary">{totalUsage}</div>
          <div className="text-xs text-muted-foreground">总使用次数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-amber-500">{tags.filter((t) => (t.usage_count || 0) > 0).length}</div>
          <div className="text-xs text-muted-foreground">活跃标签</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          {topTag ? (
            <>
              <span className="text-sm px-2 py-0.5 rounded-full text-white font-medium" style={{ backgroundColor: topTag.color }}>
                {topTag.name}
              </span>
              <div className="text-[10px] text-muted-foreground mt-1">{topTag.usage_count}次使用</div>
            </>
          ) : (
            <div className="text-sm text-muted-foreground">-</div>
          )}
          <div className="text-xs text-muted-foreground">最热门标签</div>
        </div>
      </div>

      {/* ── Toolbar ── */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索标签名称..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <Button onClick={() => setShowCreate(true)} variant="primary" className="gap-1.5">
          <Plus className="w-3 h-3" />新建标签
        </Button>
        <Button onClick={fetchTags} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
      </div>

      {/* ── Tag Grid ── */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {filtered.map((tag) => (
          <div key={tag.id} className="rounded-lg border border-border bg-card p-4 hover:bg-muted/20 transition-colors">
            <div className="flex items-center gap-3 mb-3">
              <span
                className="text-sm px-3 py-1 rounded-full text-white font-medium"
                style={{ backgroundColor: tag.color || '#6b7280' }}
              >
                {tag.name}
              </span>
              <div className="flex-1" />
              <span className="text-[10px] text-muted-foreground font-mono">{tag.id.slice(0, 8)}</span>
            </div>

            <div className="flex items-center gap-3 text-[10px] text-muted-foreground mb-3">
              <span className="flex items-center gap-1">
                <Hash className="w-2.5 h-2.5" />
                {tag.usage_count || 0} 次使用
              </span>
              <span className="flex items-center gap-1">
                <Palette className="w-2.5 h-2.5" />
                {tag.color || '#6b7280'}
              </span>
            </div>

            <div className="flex items-center gap-2">
              <Button onClick={() => setEditingTag(tag)} variant="secondary" className="flex-1">
                <Edit3 className="w-3 h-3" />编辑
              </Button>
              <button
                onClick={() => handleDelete(tag.id)}
                disabled={deleting === tag.id}
                className="flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm bg-red-500/10 text-red-500 border border-red-500/30 rounded-lg hover:bg-red-500/20 disabled:opacity-50"
              >
                {deleting === tag.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
              </button>
            </div>
          </div>
        ))}
      </div>

      {filtered.length === 0 && (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
          <Tag className="w-10 h-10 mb-2 opacity-30" />
          <p className="text-xs">{search ? '无匹配标签' : '暂无标签'}</p>
          <p className="text-[10px] mt-1">创建标签来组织知识条目</p>
        </div>
      )}

      {/* ── Dialogs ── */}
      {showCreate && (
        <TagDialog
          onClose={() => setShowCreate(false)}
          onSave={() => { setShowCreate(false); fetchTags(); }}
        />
      )}
      {editingTag && (
        <TagDialog
          tag={editingTag}
          onClose={() => setEditingTag(null)}
          onSave={() => { setEditingTag(null); fetchTags(); }}
        />
      )}

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
