'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Dna, Plus, Trash2, RefreshCw, X, Copy, Download, Upload, Play,
  Tag, FolderOpen, Filter, ChevronDown, ChevronRight, Edit3, Package, BarChart3, Eye, Settings,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

interface Template {
  template_id: string;
  name: string;
  category: string;
  description: string;
  version: string;
  author: string;
  tags: string[];
  config: Record<string, any>;
  variables: Variable[];
  usage_count: number;
  builtin: boolean;
}

interface Variable {
  name: string;
  type?: string;
  description?: string;
  default?: any;
  required?: boolean;
}

interface Category {
  name: string;
  count: number;
}

interface Stats {
  total_templates: number;
  builtin_count: number;
  user_count: number;
  total_usage_count: number;
  by_category: Record<string, number>;
  by_author: Record<string, number>;
  most_used: Template[];
}

const CATEGORY_COLORS: Record<string, string> = {
  agent: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
  knowledge_base: 'bg-emerald-500/10 text-emerald-500 border-green-500/20',
  workflow: 'bg-violet-500/10 text-violet-500 border-purple-500/20',
  skill: 'bg-amber-500/10 text-amber-500 border-orange-500/20',
};

const CATEGORY_LABELS: Record<string, string> = {
  agent: 'Agent',
  knowledge_base: '知识库',
  workflow: '工作流',
  skill: '技能',
};

export default function GenePage() {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [tagFilter, setTagFilter] = useState('');
  const [allTags, setAllTags] = useState<{ tag: string; count: number }[]>([]);

  // Dialogs
  const [selectedTemplate, setSelectedTemplate] = useState<Template | null>(null);
  const [showDetail, setShowDetail] = useState(false);
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [showCloneDialog, setShowCloneDialog] = useState(false);
  const [showInstantiateDialog, setShowInstantiateDialog] = useState(false);
  const [showImportDialog, setShowImportDialog] = useState(false);
  const [instantiateVars, setInstantiateVars] = useState<Record<string, string>>({});
  const [instantiateResult, setInstantiateResult] = useState<any>(null);
  const [cloneName, setCloneName] = useState('');
  const [cloneId, setCloneId] = useState('');
  const [importJson, setImportJson] = useState('');
  const [importOverwrite, setImportOverwrite] = useState(false);

  // Create form
  const [newTemplate, setNewTemplate] = useState({
    name: '', category: 'agent', description: '', version: '1.0.0', author: 'user',
    tags: '', config: '{}', variables: '[]',
  });

  const fetchAll = useCallback(async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams();
      if (categoryFilter) params.set('category', categoryFilter);
      if (tagFilter) params.set('tag', tagFilter);
      const qs = params.toString();
      const [tplData, catData, tagData, statsData] = await Promise.all([
        apiFetch(`/api/gene/templates${qs ? '?' + qs : ''}`),
        apiFetch('/api/gene/categories'),
        apiFetch('/api/gene/tags'),
        apiFetch('/api/gene/stats'),
      ]);
      setTemplates(Array.isArray(tplData.templates) ? tplData.templates : []);
      setCategories(Array.isArray(catData.categories) ? catData.categories : []);
      setAllTags(Array.isArray(tagData.tags) ? tagData.tags : []);
      setStats(statsData);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [categoryFilter, tagFilter]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const handleCreate = async () => {
    try {
      setError('');
      let tags: string[] = [];
      let config = {};
      let variables: Variable[] = [];
      try { tags = newTemplate.tags.split(',').map(t => t.trim()).filter(Boolean); } catch {}
      try { config = JSON.parse(newTemplate.config); } catch { setError('配置JSON格式错误'); return; }
      try { variables = JSON.parse(newTemplate.variables); } catch { setError('变量JSON格式错误'); return; }
      await apiFetch('/api/gene/templates', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newTemplate.name, category: newTemplate.category,
          description: newTemplate.description, version: newTemplate.version,
          author: newTemplate.author, tags, config, variables,
        }),
      });
      setShowCreateDialog(false);
      setNewTemplate({ name: '', category: 'agent', description: '', version: '1.0.0', author: 'user', tags: '', config: '{}', variables: '[]' });
      fetchAll();
    } catch (e: any) { setError(e.message); }
  };

  const handleDelete = async (templateId: string) => {
    if (!confirm(`确定要删除模板 "${templateId}"？`)) return;
    try {
      await apiFetch(`/api/gene/templates/${templateId}`, { method: 'DELETE' });
      setTemplates(prev => prev.filter(t => t.template_id !== templateId));
      if (selectedTemplate?.template_id === templateId) { setShowDetail(false); setSelectedTemplate(null); }
    } catch (e: any) { setError(e.message); }
  };

  const handleClone = async () => {
    if (!selectedTemplate) return;
    try {
      await apiFetch(`/api/gene/templates/${selectedTemplate.template_id}/clone`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_id: cloneId || undefined, new_name: cloneName || undefined }),
      });
      setShowCloneDialog(false);
      setCloneName('');
      setCloneId('');
      fetchAll();
    } catch (e: any) { setError(e.message); }
  };

  const handleInstantiate = async () => {
    if (!selectedTemplate) return;
    try {
      setInstantiateResult(null);
      const result = await apiFetch(`/api/gene/templates/${selectedTemplate.template_id}/instantiate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ variables: instantiateVars }),
      });
      setInstantiateResult(result);
    } catch (e: any) { setError(e.message); }
  };

  const handleExport = async (templateId: string) => {
    try {
      const data = await apiFetch(`/api/gene/templates/${templateId}/export`);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `gene-${templateId}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: any) { setError(e.message); }
  };

  const handleExportAll = async () => {
    try {
      const data = await apiFetch('/api/gene/export');
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `gene-export-${Date.now()}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: any) { setError(e.message); }
  };

  const handleImport = async () => {
    try {
      let templates: any[];
      try { templates = JSON.parse(importJson); } catch { setError('JSON格式错误'); return; }
      if (!Array.isArray(templates)) templates = [templates];
      const result = await apiFetch('/api/gene/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ templates, overwrite: importOverwrite }),
      });
      setShowImportDialog(false);
      setImportJson('');
      fetchAll();
      alert(`导入完成：${result.imported}/${result.total} 成功`);
    } catch (e: any) { setError(e.message); }
  };

  const filtered = templates.filter((t) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return t.name.toLowerCase().includes(q) || t.description.toLowerCase().includes(q) ||
      t.template_id.toLowerCase().includes(q) || t.tags.some(tag => tag.toLowerCase().includes(q));
  });

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{stats?.total_templates ?? templates.length}</div>
          <div className="text-xs text-muted-foreground">总模板数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-500">{stats?.builtin_count ?? 0}</div>
          <div className="text-xs text-muted-foreground">内置模板</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-500">{stats?.user_count ?? 0}</div>
          <div className="text-xs text-muted-foreground">用户模板</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-purple-500">{categories.length}</div>
          <div className="text-xs text-muted-foreground">分类数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-orange-500">{stats?.total_usage_count ?? 0}</div>
          <div className="text-xs text-muted-foreground">总使用次数</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索模板名称、描述、标签..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={categoryFilter}
          onChange={(e) => setCategoryFilter(e.target.value)}
        >
          <option value="">全部分类</option>
          {categories.map((c) => (
            <option key={c.name} value={c.name}>{CATEGORY_LABELS[c.name] || c.name} ({c.count})</option>
          ))}
        </select>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={tagFilter}
          onChange={(e) => setTagFilter(e.target.value)}
        >
          <option value="">全部标签</option>
          {allTags.map((t) => (
            <option key={t.tag} value={t.tag}>{t.tag} ({t.count})</option>
          ))}
        </select>
        <Button onClick={() => fetchAll()} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
        <Button onClick={() => setShowCreateDialog(true)} variant="primary" className="gap-1.5">
          <Plus className="w-3.5 h-3.5" />新建
        </Button>
        <Button onClick={() => setShowImportDialog(true)} variant="secondary">
          <Upload className="w-3.5 h-3.5" />导入
        </Button>
        <Button onClick={handleExportAll} variant="secondary">
          <Download className="w-3.5 h-3.5" />导出全部
        </Button>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 p-2 text-xs text-red-500 bg-red-500/5 rounded-md border border-red-500/20">
          <span className="flex-1">{error}</span>
          <Button variant="bare" onClick={() => setError('')}><X className="w-3 h-3" /></Button>
        </div>
      )}

      {/* Template Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {filtered.map((tpl) => (
          <div
            key={tpl.template_id}
            className="rounded-lg border border-border bg-card p-4 hover:border-primary/30 transition-colors cursor-pointer group"
            onClick={() => { setSelectedTemplate(tpl); setShowDetail(true); }}
          >
            <div className="flex items-start justify-between mb-2">
              <div className="flex items-center gap-2 min-w-0">
                <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${CATEGORY_COLORS[tpl.category] || 'bg-muted'}`}>
                  <Dna className="w-4 h-4" />
                </div>
                <div className="min-w-0">
                  <h3 className="text-sm font-medium truncate">{tpl.name}</h3>
                  <p className="text-[10px] text-muted-foreground truncate font-mono">{tpl.template_id}</p>
                </div>
              </div>
              {tpl.builtin && (
                <Badge variant="info" className="border border-blue-500/20 shrink-0">内置</Badge>
              )}
            </div>
            {tpl.description && (
              <p className="text-xs text-muted-foreground line-clamp-2 mb-2">{tpl.description}</p>
            )}
            <div className="flex items-center gap-2 flex-wrap mb-2">
              <Badge variant="bare" className={`border ${CATEGORY_COLORS[tpl.category] || 'bg-muted text-muted-foreground border-border'}`}>
                {CATEGORY_LABELS[tpl.category] || tpl.category}
              </Badge>
              {tpl.tags.slice(0, 3).map((tag) => (
                <Badge variant="muted" key={tag}>#{tag}</Badge>
              ))}
              {tpl.tags.length > 3 && <span className="text-[10px] text-muted-foreground">+{tpl.tags.length - 3}</span>}
            </div>
            <div className="flex items-center justify-between text-[10px] text-muted-foreground">
              <span>v{tpl.version} · {tpl.author}</span>
              <span>使用 {tpl.usage_count} 次</span>
            </div>
            {/* Quick actions (visible on hover) */}
            <div className="flex items-center gap-1 mt-2 opacity-0 group-hover:opacity-100 transition-opacity">
              <Button
                onClick={(e) => { e.stopPropagation(); setSelectedTemplate(tpl); setCloneName(tpl.name + ' (副本)'); setCloneId(''); setShowCloneDialog(true); }}
                variant="chip-xs" className="flex items-center gap-1 bg-muted hover:bg-muted"
                title="克隆"
              >
                <Copy className="w-3 h-3" />克隆
              </Button>
              <Button
                onClick={(e) => { e.stopPropagation(); setSelectedTemplate(tpl); setInstantiateVars({}); setInstantiateResult(null); setShowInstantiateDialog(true); }}
                variant="chip-xs" className="flex items-center gap-1 bg-muted hover:bg-muted"
                title="实例化"
              >
                <Play className="w-3 h-3" />实例化
              </Button>
              <Button
                onClick={(e) => { e.stopPropagation(); handleExport(tpl.template_id); }}
                variant="chip-xs" className="flex items-center gap-1 bg-muted hover:bg-muted"
                title="导出"
              >
                <Download className="w-3 h-3" />
              </Button>
              {!tpl.builtin && (
                <Button
                  onClick={(e) => { e.stopPropagation(); handleDelete(tpl.template_id); }}
                  variant="chip-xs" className="flex items-center gap-1 bg-red-500/10 text-red-500 hover:bg-red-500/20 ml-auto"
                  title="删除"
                >
                  <Trash2 className="w-3 h-3" />
                </Button>
              )}
            </div>
          </div>
        ))}
        {filtered.length === 0 && (
          <div className="col-span-full flex flex-col items-center justify-center py-12 text-muted-foreground">
            <Dna className="w-10 h-10 mb-2 opacity-30" />
            <p className="text-xs">{search || categoryFilter || tagFilter ? '没有匹配的模板' : '暂无模板'}</p>
          </div>
        )}
      </div>

      {/* Detail Slide-over */}
      {showDetail && selectedTemplate && (
        <div className="fixed inset-0 z-50 flex justify-end" onClick={() => setShowDetail(false)}>
          <div className="absolute inset-0 bg-black/30" />
          <div className="relative w-full max-w-lg bg-background border-l border-border overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="sticky top-0 bg-background border-b border-border p-4 flex items-center justify-between z-10">
              <h2 className="text-sm font-medium">模板详情</h2>
              <Button onClick={() => setShowDetail(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-4">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${CATEGORY_COLORS[selectedTemplate.category] || 'bg-muted'}`}>
                    <Dna className="w-5 h-5" />
                  </div>
                  <div>
                    <h3 className="text-base font-medium">{selectedTemplate.name}</h3>
                    <p className="text-xs text-muted-foreground font-mono">{selectedTemplate.template_id}</p>
                  </div>
                </div>
              </div>

              {selectedTemplate.description && (
                <div>
                  <label className="text-[10px] text-muted-foreground uppercase tracking-wider">描述</label>
                  <p className="text-xs mt-1">{selectedTemplate.description}</p>
                </div>
              )}

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-[10px] text-muted-foreground uppercase tracking-wider">分类</label>
                  <p className="text-xs mt-1">{CATEGORY_LABELS[selectedTemplate.category] || selectedTemplate.category}</p>
                </div>
                <div>
                  <label className="text-[10px] text-muted-foreground uppercase tracking-wider">版本</label>
                  <p className="text-xs mt-1">{selectedTemplate.version}</p>
                </div>
                <div>
                  <label className="text-[10px] text-muted-foreground uppercase tracking-wider">作者</label>
                  <p className="text-xs mt-1">{selectedTemplate.author}</p>
                </div>
                <div>
                  <label className="text-[10px] text-muted-foreground uppercase tracking-wider">使用次数</label>
                  <p className="text-xs mt-1">{selectedTemplate.usage_count}</p>
                </div>
              </div>

              {selectedTemplate.tags.length > 0 && (
                <div>
                  <label className="text-[10px] text-muted-foreground uppercase tracking-wider">标签</label>
                  <div className="flex flex-wrap gap-1 mt-1">
                    {selectedTemplate.tags.map((tag) => (
                      <Badge variant="muted" key={tag}>#{tag}</Badge>
                    ))}
                  </div>
                </div>
              )}

              {selectedTemplate.variables.length > 0 && (
                <div>
                  <label className="text-[10px] text-muted-foreground uppercase tracking-wider">变量</label>
                  <div className="mt-1 space-y-1">
                    {selectedTemplate.variables.map((v, i) => (
                      <div key={i} className="flex items-center gap-2 text-xs p-2 bg-muted rounded">
                        <code className="font-mono text-primary">{v.name}</code>
                        {v.type && <span className="text-[10px] text-muted-foreground">({v.type})</span>}
                        {v.required && <span className="text-[10px] text-red-500">必填</span>}
                        {v.description && <span className="text-[10px] text-muted-foreground flex-1 truncate">{v.description}</span>}
                        {v.default !== undefined && <span className="text-[10px] text-muted-foreground">默认: {String(v.default)}</span>}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div>
                <label className="text-[10px] text-muted-foreground uppercase tracking-wider">配置</label>
                <pre className="mt-1 text-[11px] bg-muted rounded p-3 overflow-x-auto max-h-60">
                  {JSON.stringify(selectedTemplate.config, null, 2)}
                </pre>
              </div>

              {/* Actions */}
              <div className="flex flex-wrap gap-2 pt-2 border-t border-border">
                <Button onClick={() => { setInstantiateVars({}); setInstantiateResult(null); setShowInstantiateDialog(true); }} variant="primary" className="gap-1.5">
                  <Play className="w-3.5 h-3.5" />实例化
                </Button>
                <Button onClick={() => { setCloneName(selectedTemplate.name + ' (副本)'); setCloneId(''); setShowCloneDialog(true); }} variant="secondary">
                  <Copy className="w-3.5 h-3.5" />克隆
                </Button>
                <Button onClick={() => handleExport(selectedTemplate.template_id)} variant="secondary">
                  <Download className="w-3.5 h-3.5" />导出
                </Button>
                {!selectedTemplate.builtin && (
                  <Button
                    onClick={() => { handleDelete(selectedTemplate.template_id); }}
                    variant="chip-sm" className="flex items-center gap-1.5 bg-red-500/10 text-red-500 border border-red-500/20 hover:bg-red-500/20"
                  >
                    <Trash2 className="w-3.5 h-3.5" />删除
                  </Button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Create Dialog */}
      {showCreateDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center" onClick={() => setShowCreateDialog(false)}>
          <div className="absolute inset-0 bg-black/30" />
          <div className="relative w-full max-w-lg bg-background border border-border rounded-lg shadow-lg max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="sticky top-0 bg-background border-b border-border p-4 flex items-center justify-between z-10">
              <h2 className="text-sm font-medium">新建模板</h2>
              <Button onClick={() => setShowCreateDialog(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">模板名称 *</label>
                <input className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={newTemplate.name} onChange={(e) => setNewTemplate(p => ({ ...p, name: e.target.value }))} placeholder="我的模板" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">分类 *</label>
                  <select className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none" value={newTemplate.category} onChange={(e) => setNewTemplate(p => ({ ...p, category: e.target.value }))}>
                    <option value="agent">Agent</option>
                    <option value="knowledge_base">知识库</option>
                    <option value="workflow">工作流</option>
                    <option value="skill">技能</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">版本</label>
                  <input className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none" value={newTemplate.version} onChange={(e) => setNewTemplate(p => ({ ...p, version: e.target.value }))} />
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">描述</label>
                <textarea className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 h-20 resize-none" value={newTemplate.description} onChange={(e) => setNewTemplate(p => ({ ...p, description: e.target.value }))} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">作者</label>
                  <input className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none" value={newTemplate.author} onChange={(e) => setNewTemplate(p => ({ ...p, author: e.target.value }))} />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">标签 (逗号分隔)</label>
                  <input className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none" value={newTemplate.tags} onChange={(e) => setNewTemplate(p => ({ ...p, tags: e.target.value }))} placeholder="tag1, tag2" />
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">配置 (JSON)</label>
                <textarea className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none font-mono h-24 resize-none" value={newTemplate.config} onChange={(e) => setNewTemplate(p => ({ ...p, config: e.target.value }))} />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">变量 (JSON数组)</label>
                <textarea className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none font-mono h-20 resize-none" value={newTemplate.variables} onChange={(e) => setNewTemplate(p => ({ ...p, variables: e.target.value }))} placeholder='[{"name": "var1", "type": "string", "description": "变量说明"}]' />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowCreateDialog(false)} variant="secondary" className="px-4">取消</Button>
                <Button onClick={handleCreate} disabled={!newTemplate.name} variant="primary" className="px-4 py-1.5">创建</Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Clone Dialog */}
      {showCloneDialog && selectedTemplate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center" onClick={() => setShowCloneDialog(false)}>
          <div className="absolute inset-0 bg-black/30" />
          <div className="relative w-full max-w-md bg-background border border-border rounded-lg shadow-lg" onClick={(e) => e.stopPropagation()}>
            <div className="p-4 border-b border-border flex items-center justify-between">
              <h2 className="text-sm font-medium">克隆模板</h2>
              <Button onClick={() => setShowCloneDialog(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <p className="text-xs text-muted-foreground">源模板: <code className="font-mono">{selectedTemplate.template_id}</code></p>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">新名称</label>
                <input className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none" value={cloneName} onChange={(e) => setCloneName(e.target.value)} placeholder={selectedTemplate.name + ' (副本)'} />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">新ID (可选)</label>
                <input className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none font-mono" value={cloneId} onChange={(e) => setCloneId(e.target.value)} placeholder="自动生成" />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowCloneDialog(false)} variant="secondary" className="px-4">取消</Button>
                <Button onClick={handleClone} variant="primary" className="px-4 py-1.5">克隆</Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Instantiate Dialog */}
      {showInstantiateDialog && selectedTemplate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center" onClick={() => setShowInstantiateDialog(false)}>
          <div className="absolute inset-0 bg-black/30" />
          <div className="relative w-full max-w-lg bg-background border border-border rounded-lg shadow-lg max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="sticky top-0 bg-background border-b border-border p-4 flex items-center justify-between z-10">
              <h2 className="text-sm font-medium">实例化模板</h2>
              <Button onClick={() => setShowInstantiateDialog(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <p className="text-xs text-muted-foreground">模板: <code className="font-mono">{selectedTemplate.template_id}</code></p>
              {selectedTemplate.variables.length > 0 ? (
                <div className="space-y-2">
                  <label className="text-xs font-medium">填写变量</label>
                  {selectedTemplate.variables.map((v) => (
                    <div key={v.name}>
                      <label className="text-xs text-muted-foreground mb-1 flex items-center gap-1">
                        {v.name}
                        {v.required && <span className="text-red-500">*</span>}
                        {v.type && <span className="text-[10px]">({v.type})</span>}
                        {v.description && <span className="text-[10px]">— {v.description}</span>}
                      </label>
                      <input
                        className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none font-mono"
                        value={instantiateVars[v.name] || ''}
                        onChange={(e) => setInstantiateVars(prev => ({ ...prev, [v.name]: e.target.value }))}
                        placeholder={v.default !== undefined ? String(v.default) : ''}
                      />
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">此模板没有可配置变量。</p>
              )}
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowInstantiateDialog(false)} variant="secondary" className="px-4">取消</Button>
                <Button onClick={handleInstantiate} variant="primary" className="px-4 py-1.5">实例化</Button>
              </div>
              {instantiateResult && (
                <div className="mt-3">
                  <label className="text-xs font-medium mb-1 block">实例化结果</label>
                  <pre className="text-[11px] bg-green-500/5 border border-green-500/20 rounded p-3 overflow-x-auto max-h-60">
                    {JSON.stringify(instantiateResult, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Import Dialog */}
      {showImportDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center" onClick={() => setShowImportDialog(false)}>
          <div className="absolute inset-0 bg-black/30" />
          <div className="relative w-full max-w-lg bg-background border border-border rounded-lg shadow-lg" onClick={(e) => e.stopPropagation()}>
            <div className="p-4 border-b border-border flex items-center justify-between">
              <h2 className="text-sm font-medium">导入模板</h2>
              <Button onClick={() => setShowImportDialog(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">模板JSON</label>
                <textarea
                  className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none font-mono h-40 resize-none"
                  value={importJson}
                  onChange={(e) => setImportJson(e.target.value)}
                  placeholder='[{"name": "...", "category": "agent", ...}]'
                />
              </div>
              <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                <input type="checkbox" checked={importOverwrite} onChange={(e) => setImportOverwrite(e.target.checked)} className="rounded" />
                覆盖已有模板
              </label>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowImportDialog(false)} variant="secondary" className="px-4">取消</Button>
                <Button onClick={handleImport} disabled={!importJson.trim()} variant="primary" className="px-4 py-1.5">导入</Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
