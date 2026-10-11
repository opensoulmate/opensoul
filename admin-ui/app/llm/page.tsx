'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Settings, RefreshCw, X, Save, Zap, AlertCircle, CheckCircle,
  Loader2, Plus, Trash2, Server, Key, Cpu, Activity
} from 'lucide-react';
import { Button } from '@/components/ui/button';

interface LLMConfig {
  base_url: string;
  api_key: string;
  model: string;
}

interface Provider {
  name: string;
  base_url: string;
  models: Record<string, string>;
  enabled: boolean;
  priority: number;
  consecutive_failures: number;
}

export default function LlmPage() {
  // LLM Config
  const [config, setConfig] = useState<LLMConfig>({ base_url: '', api_key: '', model: '' });
  const [configLoading, setConfigLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editConfig, setEditConfig] = useState<LLMConfig>({ base_url: '', api_key: '', model: '' });

  // Providers
  const [providers, setProviders] = useState<Provider[]>([]);
  const [providersLoading, setProvidersLoading] = useState(true);

  // Test
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ status: string; reply?: string; error?: string } | null>(null);

  // Add provider dialog
  const [showAddProvider, setShowAddProvider] = useState(false);
  const [newProvider, setNewProvider] = useState({ name: '', base_url: '', models: '{}', priority: '0' });

  // Error
  const [error, setError] = useState('');

  const fetchConfig = useCallback(async () => {
    try {
      setConfigLoading(true);
      const data = await apiFetch('/api/llm/config');
      setConfig(data);
      setEditConfig({ base_url: data.base_url || '', api_key: '', model: data.model || '' });
    } catch (e: any) {
      setError(e.message);
    } finally {
      setConfigLoading(false);
    }
  }, []);

  const fetchProviders = useCallback(async () => {
    try {
      setProvidersLoading(true);
      const data = await apiFetch('/api/gland/providers');
      setProviders(Array.isArray(data) ? data : []);
    } catch (e: any) {
      // silent - gland might not be available
    } finally {
      setProvidersLoading(false);
    }
  }, []);

  useEffect(() => { fetchConfig(); fetchProviders(); }, [fetchConfig, fetchProviders]);

  const handleSaveConfig = async () => {
    try {
      setSaving(true);
      const body: any = {};
      if (editConfig.base_url) body.base_url = editConfig.base_url;
      if (editConfig.api_key) body.api_key = editConfig.api_key;
      if (editConfig.model) body.model = editConfig.model;
      await apiFetch('/api/llm/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      fetchConfig();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    try {
      setTesting(true);
      setTestResult(null);
      const body: any = {};
      if (editConfig.base_url) body.base_url = editConfig.base_url;
      if (editConfig.api_key) body.api_key = editConfig.api_key;
      if (editConfig.model) body.model = editConfig.model;
      const data = await apiFetch('/api/llm/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      setTestResult({ status: 'ok', reply: data.reply });
    } catch (e: any) {
      setTestResult({ status: 'error', error: e.message });
    } finally {
      setTesting(false);
    }
  };

  const handleAddProvider = async () => {
    try {
      let models: Record<string, string> = {};
      try { models = JSON.parse(newProvider.models); } catch { models = {}; }
      await apiFetch('/api/gland/providers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newProvider.name,
          base_url: newProvider.base_url,
          models,
          priority: parseInt(newProvider.priority) || 0,
        }),
      });
      setShowAddProvider(false);
      setNewProvider({ name: '', base_url: '', models: '{}', priority: '0' });
      fetchProviders();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleDeleteProvider = async (name: string) => {
    if (!confirm(`确定要删除 provider「${name}」？`)) return;
    try {
      await apiFetch(`/api/gland/providers/${name}`, { method: 'DELETE' });
      fetchProviders();
    } catch (e: any) {
      setError(e.message);
    }
  };

  if (configLoading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 p-2 text-xs text-red-500 bg-red-500/5 rounded-md border border-red-500/20">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <button onClick={() => setError('')}><X className="w-3 h-3" /></button>
        </div>
      )}

      {/* LLM Config Card */}
      <div className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-center gap-2 mb-4">
          <Cpu className="w-4 h-4 text-primary" />
          <h2 className="text-sm font-medium">LLM 配置</h2>
          <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-muted text-muted-foreground">主模型</span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <label className="text-xs text-muted-foreground">Base URL</label>
            <input
              className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono"
              value={editConfig.base_url}
              onChange={(e) => setEditConfig((c) => ({ ...c, base_url: e.target.value }))}
              placeholder={config.base_url || 'https://api.openai.com/v1'}
            />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">API Key</label>
            <input
              className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono"
              type="password"
              value={editConfig.api_key}
              onChange={(e) => setEditConfig((c) => ({ ...c, api_key: e.target.value }))}
              placeholder={config.api_key || 'sk-...'}
            />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">模型</label>
            <input
              className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
              value={editConfig.model}
              onChange={(e) => setEditConfig((c) => ({ ...c, model: e.target.value }))}
              placeholder={config.model || 'gpt-4o'}
            />
          </div>
        </div>
        <div className="flex items-center gap-2 mt-3">
          <Button onClick={handleSaveConfig}
            disabled={saving} variant="primary">
            {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
            {saving ? '保存中...' : '保存'}
          </Button>
          <button
            onClick={handleTest}
            disabled={testing}
            className="flex items-center gap-1 px-3 py-1.5 text-xs lg:text-sm bg-blue-500/10 text-blue-500 border border-blue-500/20 rounded-lg hover:bg-blue-500/20 disabled:opacity-50"
          >
            {testing ? <Loader2 className="w-3 h-3 animate-spin" /> : <Zap className="w-3 h-3" />}
            {testing ? '测试中...' : '测试连接'}
          </button>
        </div>
        {testResult && (
          <div className={`mt-3 p-2 rounded text-xs ${testResult.status === 'ok' ? 'bg-green-500/5 text-green-600 border border-green-500/20' : 'bg-red-500/5 text-red-500 border border-red-500/20'}`}>
            {testResult.status === 'ok' ? (
              <div><CheckCircle className="w-3 h-3 inline mr-1" />连接成功: {testResult.reply}</div>
            ) : (
              <div><AlertCircle className="w-3 h-3 inline mr-1" />{testResult.error}</div>
            )}
          </div>
        )}
      </div>

      {/* Providers */}
      <div className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Server className="w-4 h-4 text-primary" />
            <h2 className="text-sm font-medium">Provider 管理</h2>
            <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-muted text-muted-foreground">{providers.length} 个</span>
          </div>
          <Button onClick={() => setShowAddProvider(true)} variant="primary">
            <Plus className="w-3 h-3" />添加Provider
          </Button>
        </div>

        {providersLoading ? (
          <div className="text-xs text-muted-foreground text-center py-4">加载中...</div>
        ) : providers.length === 0 ? (
          <div className="text-xs text-muted-foreground text-center py-8">暂无 Provider 配置</div>
        ) : (
          <div className="space-y-2">
            {providers.map((p) => (
              <div key={p.name} className="rounded border border-border p-3 hover:bg-muted/30 transition-colors">
                <div className="flex items-start justify-between">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <h3 className="text-xs font-medium">{p.name}</h3>
                      <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${p.enabled ? 'bg-emerald-500/10 text-emerald-500' : 'bg-muted text-muted-foreground'}`}>
                        {p.enabled ? '已启用' : '已禁用'}
                      </span>
                      <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-primary/10 text-primary">优先级: {p.priority}</span>
                      {p.consecutive_failures > 0 && (
                        <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-red-500/10 text-red-500">失败: {p.consecutive_failures}</span>
                      )}
                    </div>
                    <div className="text-[10px] text-muted-foreground mt-1 font-mono">{p.base_url}</div>
                    {Object.keys(p.models).length > 0 && (
                      <div className="flex items-center gap-2 mt-1">
                        {Object.entries(p.models).map(([task, model]) => (
                          <span key={task} className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-muted text-muted-foreground">
                            {task}: {model}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  <button onClick={() => handleDeleteProvider(p.name)} className="p-1.5 rounded-md hover:bg-red-500/10" title="删除">
                    <Trash2 className="w-3.5 h-3.5 text-red-500" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Add Provider Dialog */}
      {showAddProvider && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowAddProvider(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">添加 Provider</h2>
              <button onClick={() => setShowAddProvider(false)} className="p-1 rounded-md hover:bg-muted"><X className="w-4 h-4" /></button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">名称 *</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={newProvider.name} onChange={(e) => setNewProvider((f) => ({ ...f, name: e.target.value }))} placeholder="openai" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">Base URL *</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono" value={newProvider.base_url} onChange={(e) => setNewProvider((f) => ({ ...f, base_url: e.target.value }))} placeholder="https://api.openai.com/v1" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground">模型映射 (JSON)</label>
                  <textarea className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono h-16 resize-none" value={newProvider.models} onChange={(e) => setNewProvider((f) => ({ ...f, models: e.target.value }))} placeholder='{"chat": "gpt-4o", "embedding": "text-embedding-3-small"}' />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">优先级 (越小越高)</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" type="number" value={newProvider.priority} onChange={(e) => setNewProvider((f) => ({ ...f, priority: e.target.value }))} />
                </div>
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowAddProvider(false)} variant="secondary">取消</Button>
                <Button onClick={handleAddProvider} disabled={!newProvider.name || !newProvider.base_url} variant="primary">
                  <Plus className="w-3 h-3" />添加
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
