'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Globe, Database, FolderOpen, Search, RefreshCw, X, Play, Settings,
  Loader2, AlertCircle, CheckCircle, Server, Terminal, Radio, Plug,
  Send, ChevronDown, ChevronRight, Table, FileText, Eye, Zap, Cpu,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

// ── Types ──────────────────────────────────────────────────────

interface ScanResult {
  scan_time: number;
  processes: ProcessInfo[];
  cli_tools: CLITool[];
  services: ServiceInfo[];
  adapters: string[];
}

interface ProcessInfo {
  pid: number;
  name: string;
  cmd: string;
  cpu: number;
  memory: number;
}

interface CLITool {
  name: string;
  path: string;
  version: string;
}

interface ServiceInfo {
  name: string;
  host: string;
  port: number;
  protocol: string;
}

interface AdapterInfo {
  name: string;
  description: string;
  capabilities: string[];
  configured: boolean;
}

type Tab = 'discovery' | 'rest' | 'database' | 'filesystem';

// ── Main Page ──────────────────────────────────────────────────

export default function ConnectorsPage() {
  const [tab, setTab] = useState<Tab>('discovery');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Discovery
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [scanning, setScanning] = useState(false);
  const [adapters, setAdapters] = useState<AdapterInfo[]>([]);

  // REST
  const [restConfig, setRestConfig] = useState({ base_url: '', headers: '{}', auth_token: '', timeout: '30' });
  const [restMethod, setRestMethod] = useState('GET');
  const [restPath, setRestPath] = useState('');
  const [restBody, setRestBody] = useState('');
  const [restResponse, setRestResponse] = useState<any>(null);
  const [restLoading, setRestLoading] = useState(false);
  const [probeResult, setProbeResult] = useState<any>(null);

  // Database
  const [dbConfig, setDbConfig] = useState({ db_type: 'sqlite', connection_string: '', max_queries: '100' });
  const [dbQuery, setDbQuery] = useState('');
  const [dbResult, setDbResult] = useState<any>(null);
  const [dbTables, setDbTables] = useState<any>(null);
  const [dbDescribe, setDbDescribe] = useState<any>(null);
  const [dbLoading, setDbLoading] = useState(false);

  // Filesystem
  const [fsConfig, setFsConfig] = useState({ root_path: '', watch: 'false' });
  const [fsPath, setFsPath] = useState('/');
  const [fsPattern, setFsPattern] = useState('');
  const [fsResult, setFsResult] = useState<any>(null);
  const [fsLoading, setFsLoading] = useState(false);

  // ── Fetch ──────────────────────────────────────────────────

  const fetchAdapters = useCallback(async () => {
    try {
      const data = await apiFetch('/api/soma/discovery/adapters');
      setAdapters(Array.isArray(data) ? data : []);
    } catch { /* ignore */ }
  }, []);

  const fetchInit = useCallback(async () => {
    setLoading(true);
    await fetchAdapters();
    setLoading(false);
  }, [fetchAdapters]);

  useEffect(() => { fetchInit(); }, [fetchInit]);

  // ── Actions ────────────────────────────────────────────────

  const handleScan = async () => {
    try {
      setScanning(true);
      setError('');
      const data = await apiFetch('/api/soma/discovery/scan');
      setScanResult(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setScanning(false);
    }
  };

  const handleRestConfigure = async () => {
    try {
      setRestLoading(true);
      setError('');
      let headers = {};
      try { headers = JSON.parse(restConfig.headers); } catch {}
      await apiFetch('/api/soma/discovery/adapters/rest/configure', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          base_url: restConfig.base_url,
          headers,
          auth_token: restConfig.auth_token || undefined,
          timeout: parseInt(restConfig.timeout) || 30,
        }),
      });
      fetchAdapters();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRestLoading(false);
    }
  };

  const handleRestProbe = async () => {
    try {
      setRestLoading(true);
      setProbeResult(null);
      let headers = {};
      try { headers = JSON.parse(restConfig.headers); } catch {}
      const data = await apiFetch('/api/soma/discovery/adapters/rest/probe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          base_url: restConfig.base_url,
          headers,
          auth_token: restConfig.auth_token || undefined,
        }),
      });
      setProbeResult(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRestLoading(false);
    }
  };

  const handleRestRequest = async () => {
    if (!restPath) return;
    try {
      setRestLoading(true);
      setRestResponse(null);
      let body = undefined;
      if (restBody) try { body = JSON.parse(restBody); } catch { body = restBody; }
      const data = await apiFetch('/api/soma/discovery/adapters/rest/request', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          method: restMethod,
          path: restPath,
          body: body || undefined,
        }),
      });
      setRestResponse(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRestLoading(false);
    }
  };

  const handleDbConfigure = async () => {
    try {
      setDbLoading(true);
      setError('');
      await apiFetch('/api/soma/discovery/adapters/database/configure', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          db_type: dbConfig.db_type,
          connection_string: dbConfig.connection_string,
          max_queries: parseInt(dbConfig.max_queries) || 100,
        }),
      });
      fetchAdapters();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setDbLoading(false);
    }
  };

  const handleDbQuery = async () => {
    if (!dbQuery) return;
    try {
      setDbLoading(true);
      setDbResult(null);
      const data = await apiFetch('/api/soma/discovery/adapters/database/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sql: dbQuery }),
      });
      setDbResult(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setDbLoading(false);
    }
  };

  const handleDbTables = async () => {
    try {
      setDbLoading(true);
      setDbTables(null);
      const data = await apiFetch('/api/soma/discovery/adapters/database/tables', { method: 'POST' });
      setDbTables(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setDbLoading(false);
    }
  };

  const handleDbDescribe = async (table: string) => {
    try {
      setDbDescribe(null);
      const data = await apiFetch('/api/soma/discovery/adapters/database/describe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ table }),
      });
      setDbDescribe(data);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleFsConfigure = async () => {
    try {
      setFsLoading(true);
      setError('');
      await apiFetch('/api/soma/discovery/adapters/filesystem/configure', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          root_path: fsConfig.root_path,
          watch: fsConfig.watch === 'true',
        }),
      });
      fetchAdapters();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setFsLoading(false);
    }
  };

  const handleFsList = async () => {
    try {
      setFsLoading(true);
      setFsResult(null);
      const data = await apiFetch(`/api/soma/discovery/adapters/filesystem/list?path=${encodeURIComponent(fsPath)}`);
      setFsResult({ type: 'list', data });
    } catch (e: any) {
      setError(e.message);
    } finally {
      setFsLoading(false);
    }
  };

  const handleFsRead = async () => {
    try {
      setFsLoading(true);
      setFsResult(null);
      const data = await apiFetch('/api/soma/discovery/adapters/filesystem/read', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: fsPath }),
      });
      setFsResult({ type: 'read', data });
    } catch (e: any) {
      setError(e.message);
    } finally {
      setFsLoading(false);
    }
  };

  const handleFsSearch = async () => {
    if (!fsPattern) return;
    try {
      setFsLoading(true);
      setFsResult(null);
      const data = await apiFetch('/api/soma/discovery/adapters/filesystem/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pattern: fsPattern, path: fsPath || '/' }),
      });
      setFsResult({ type: 'search', data });
    } catch (e: any) {
      setError(e.message);
    } finally {
      setFsLoading(false);
    }
  };

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

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

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><Plug className="w-3.5 h-3.5" />适配器</div>
          <div className="text-lg font-bold">{adapters.length}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><CheckCircle className="w-3.5 h-3.5 text-green-500" />已配置</div>
          <div className="text-lg font-bold text-green-600">{adapters.filter(a => a.configured).length}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><Terminal className="w-3.5 h-3.5" />CLI工具</div>
          <div className="text-lg font-bold">{scanResult?.cli_tools.length ?? '—'}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><Radio className="w-3.5 h-3.5" />服务</div>
          <div className="text-lg font-bold">{scanResult?.services.length ?? '—'}</div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-border">
        {([
          { key: 'discovery', label: '系统发现', icon: Search },
          { key: 'rest', label: 'REST适配器', icon: Globe },
          { key: 'database', label: '数据库适配器', icon: Database },
          { key: 'filesystem', label: '文件系统适配器', icon: FolderOpen },
        ] as { key: Tab; label: string; icon: any }[]).map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex items-center gap-1.5 px-3 py-2 text-xs lg:text-sm font-medium border-b-2 transition-colors ${
              tab === t.key
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            <t.icon className="w-3.5 h-3.5" />
            {t.label}
          </button>
        ))}
        <div className="flex-1" />
        <Button onClick={handleScan}
          disabled={scanning} variant="primary" className="gap-1.5 mb-1">
          {scanning ? <Loader2 className="w-3 h-3 animate-spin" /> : <Search className="w-3 h-3" />}
          {scanning ? '扫描中...' : '系统扫描'}
        </Button>
      </div>

      {/* ── Discovery Tab ── */}
      {tab === 'discovery' && (
        <div className="space-y-4">
          {/* Adapters */}
          <div className="rounded-lg border border-border bg-card p-4">
            <h3 className="text-xs font-medium text-muted-foreground mb-3">已注册适配器</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
              {adapters.map((adapter) => (
                <div key={adapter.name} className="rounded border border-border p-3">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-sm font-medium">{adapter.name}</span>
                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${adapter.configured ? 'bg-emerald-500/10 text-emerald-500' : 'bg-muted text-muted-foreground'}`}>
                      {adapter.configured ? '已配置' : '未配置'}
                    </span>
                  </div>
                  <p className="text-[10px] text-muted-foreground">{adapter.description}</p>
                  {adapter.capabilities.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1.5">
                      {adapter.capabilities.map((cap) => (
                        <Badge key={cap} variant="default">{cap}</Badge>
                      ))}
                    </div>
                  )}
                </div>
              ))}
              {adapters.length === 0 && <div className="text-xs text-muted-foreground col-span-full text-center py-4">暂无适配器</div>}
            </div>
          </div>

          {/* Scan Results */}
          {scanResult && (
            <>
              <div className="text-[10px] text-muted-foreground">扫描耗时: {scanResult.scan_time}s</div>

              {/* Processes */}
              <div className="rounded-lg border border-border bg-card p-4">
                <h3 className="text-xs font-medium text-muted-foreground mb-3">
                  运行进程 <Badge variant="muted" className="ml-1">{scanResult.processes.length}</Badge>
                </h3>
                <div className="max-h-[200px] overflow-auto space-y-1">
                  {scanResult.processes.slice(0, 30).map((p) => (
                    <div key={p.pid} className="flex items-center gap-2 text-[10px] py-1 border-b border-border last:border-0">
                      <span className="text-muted-foreground w-12 shrink-0">PID {p.pid}</span>
                      <span className="font-medium flex-1 truncate">{p.name}</span>
                      <span className="text-muted-foreground">CPU {p.cpu.toFixed(1)}%</span>
                      <span className="text-muted-foreground">MEM {p.memory.toFixed(1)}%</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* CLI Tools */}
              <div className="rounded-lg border border-border bg-card p-4">
                <h3 className="text-xs font-medium text-muted-foreground mb-3">
                  CLI工具 <Badge variant="muted" className="ml-1">{scanResult.cli_tools.length}</Badge>
                </h3>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-1.5">
                  {scanResult.cli_tools.map((tool) => (
                    <div key={tool.name} className="rounded border border-border p-2 text-[10px]">
                      <span className="font-medium">{tool.name}</span>
                      {tool.version && <span className="text-muted-foreground ml-1">v{tool.version}</span>}
                    </div>
                  ))}
                </div>
              </div>

              {/* Services */}
              <div className="rounded-lg border border-border bg-card p-4">
                <h3 className="text-xs font-medium text-muted-foreground mb-3">
                  监听服务 <Badge variant="muted" className="ml-1">{scanResult.services.length}</Badge>
                </h3>
                <div className="max-h-[200px] overflow-auto space-y-1">
                  {scanResult.services.map((s, i) => (
                    <div key={i} className="flex items-center gap-2 text-[10px] py-1 border-b border-border last:border-0">
                      <span className="font-medium flex-1 truncate">{s.name || s.host}</span>
                      <span className="font-mono text-muted-foreground">{s.host}:{s.port}</span>
                      <Badge variant="muted">{s.protocol}</Badge>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {/* ── REST Tab ── */}
      {tab === 'rest' && (
        <div className="space-y-4">
          {/* Configure */}
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-center gap-2 mb-3">
              <Settings className="w-4 h-4 text-primary" />
              <h3 className="text-sm font-medium">REST适配器配置</h3>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-muted-foreground">Base URL *</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono"
                  value={restConfig.base_url} onChange={(e) => setRestConfig((c) => ({ ...c, base_url: e.target.value }))}
                  placeholder="https://api.example.com" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">Auth Token</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono"
                  type="password" value={restConfig.auth_token} onChange={(e) => setRestConfig((c) => ({ ...c, auth_token: e.target.value }))}
                  placeholder="Bearer token" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">Headers (JSON)</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono"
                  value={restConfig.headers} onChange={(e) => setRestConfig((c) => ({ ...c, headers: e.target.value }))}
                  placeholder='{}' />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">超时(秒)</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  type="number" value={restConfig.timeout} onChange={(e) => setRestConfig((c) => ({ ...c, timeout: e.target.value }))} />
              </div>
            </div>
            <div className="flex items-center gap-2 mt-3">
              <Button onClick={handleRestConfigure} disabled={restLoading || !restConfig.base_url} variant="primary">
                {restLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Zap className="w-3 h-3" />}配置
              </Button>
              <button onClick={handleRestProbe} disabled={restLoading || !restConfig.base_url}
                className="flex items-center gap-1 px-3 py-1.5 text-xs lg:text-sm bg-blue-500/10 text-blue-500 border border-blue-500/20 rounded-lg hover:bg-blue-500/20 disabled:opacity-50">
                <Eye className="w-3 h-3" />探测OpenAPI
              </button>
            </div>
            {probeResult && (
              <div className="mt-3 p-2 rounded bg-muted text-xs font-mono max-h-[200px] overflow-auto">
                <pre>{JSON.stringify(probeResult, null, 2)}</pre>
              </div>
            )}
          </div>

          {/* Request */}
          <div className="rounded-lg border border-border bg-card p-4">
            <h3 className="text-xs font-medium text-muted-foreground mb-3">发送请求</h3>
            <div className="flex items-center gap-2 mb-3">
              <select className="px-2 py-1.5 text-xs bg-muted border border-border rounded-md"
                value={restMethod} onChange={(e) => setRestMethod(e.target.value)}>
                <option>GET</option><option>POST</option><option>PUT</option><option>DELETE</option><option>PATCH</option>
              </select>
              <input className="flex-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono"
                value={restPath} onChange={(e) => setRestPath(e.target.value)} placeholder="/api/v1/resource" />
              <Button onClick={handleRestRequest} disabled={restLoading || !restPath} variant="primary">
                {restLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Send className="w-3 h-3" />}发送
              </Button>
            </div>
            {['POST', 'PUT', 'PATCH'].includes(restMethod) && (
              <textarea className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono h-20 resize-none mb-3"
                value={restBody} onChange={(e) => setRestBody(e.target.value)} placeholder='{"key": "value"}' />
            )}
            {restResponse && (
              <div className="p-2 rounded bg-muted text-xs font-mono max-h-[300px] overflow-auto">
                <pre>{JSON.stringify(restResponse, null, 2)}</pre>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Database Tab ── */}
      {tab === 'database' && (
        <div className="space-y-4">
          {/* Configure */}
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-center gap-2 mb-3">
              <Settings className="w-4 h-4 text-primary" />
              <h3 className="text-sm font-medium">数据库适配器配置</h3>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <div>
                <label className="text-xs text-muted-foreground">数据库类型</label>
                <select className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  value={dbConfig.db_type} onChange={(e) => setDbConfig((c) => ({ ...c, db_type: e.target.value }))}>
                  <option value="sqlite">SQLite</option>
                  <option value="postgresql">PostgreSQL</option>
                </select>
              </div>
              <div className="md:col-span-2">
                <label className="text-xs text-muted-foreground">连接字符串</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono"
                  value={dbConfig.connection_string} onChange={(e) => setDbConfig((c) => ({ ...c, connection_string: e.target.value }))}
                  placeholder="/path/to/db.sqlite 或 postgresql://user:pass@host/db" />
              </div>
            </div>
            <Button onClick={handleDbConfigure} disabled={dbLoading || !dbConfig.connection_string} variant="primary" className="mt-3">
              {dbLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Zap className="w-3 h-3" />}配置连接
            </Button>
          </div>

          {/* Query */}
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-xs font-medium text-muted-foreground">SQL查询</h3>
              <Button onClick={handleDbTables} variant="secondary" className="gap-1 px-2 py-1 text-[10px]">
                <Table className="w-3 h-3" />查看表
              </Button>
            </div>
            <textarea className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono h-20 resize-none mb-2"
              value={dbQuery} onChange={(e) => setDbQuery(e.target.value)} placeholder="SELECT * FROM table_name LIMIT 100" />
            <Button onClick={handleDbQuery} disabled={dbLoading || !dbQuery} variant="primary">
              {dbLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}执行查询
            </Button>

            {/* Tables */}
            {dbTables && (
              <div className="mt-3 p-2 rounded bg-muted text-xs">
                <p className="text-muted-foreground mb-1">表列表:</p>
                <div className="flex flex-wrap gap-1">
                  {(Array.isArray(dbTables) ? dbTables : dbTables.tables || []).map((t: any) => {
                    const name = typeof t === 'string' ? t : t.name || t.table_name;
                    return (
                      <button key={name} onClick={() => handleDbDescribe(name)}
                        className="text-[10px] px-2 py-1 rounded-lg bg-primary/10 text-primary hover:bg-primary/20">
                        {name}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Describe */}
            {dbDescribe && (
              <div className="mt-2 p-2 rounded bg-muted text-xs font-mono max-h-[200px] overflow-auto">
                <pre>{JSON.stringify(dbDescribe, null, 2)}</pre>
              </div>
            )}

            {/* Query Result */}
            {dbResult && (
              <div className="mt-3 p-2 rounded bg-muted text-xs font-mono max-h-[300px] overflow-auto">
                <pre>{JSON.stringify(dbResult, null, 2)}</pre>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Filesystem Tab ── */}
      {tab === 'filesystem' && (
        <div className="space-y-4">
          {/* Configure */}
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-center gap-2 mb-3">
              <Settings className="w-4 h-4 text-primary" />
              <h3 className="text-sm font-medium">文件系统适配器配置</h3>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-muted-foreground">根路径 *</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono"
                  value={fsConfig.root_path} onChange={(e) => setFsConfig((c) => ({ ...c, root_path: e.target.value }))}
                  placeholder="/home/user/data" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">文件监控</label>
                <select className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  value={fsConfig.watch} onChange={(e) => setFsConfig((c) => ({ ...c, watch: e.target.value }))}>
                  <option value="false">关闭</option>
                  <option value="true">开启</option>
                </select>
              </div>
            </div>
            <Button onClick={handleFsConfigure} disabled={fsLoading || !fsConfig.root_path} variant="primary" className="mt-3">
              {fsLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Zap className="w-3 h-3" />}配置
            </Button>
          </div>

          {/* Browse */}
          <div className="rounded-lg border border-border bg-card p-4">
            <h3 className="text-xs font-medium text-muted-foreground mb-3">文件浏览</h3>
            <div className="flex items-center gap-2 mb-3">
              <input className="flex-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono"
                value={fsPath} onChange={(e) => setFsPath(e.target.value)} placeholder="/" />
              <Button onClick={handleFsList} disabled={fsLoading} variant="primary">
                <FolderOpen className="w-3 h-3" />列出
              </Button>
              <button onClick={handleFsRead} disabled={fsLoading}
                className="flex items-center gap-1 px-3 py-1.5 text-xs lg:text-sm bg-blue-500/10 text-blue-500 border border-blue-500/20 rounded-lg hover:bg-blue-500/20 disabled:opacity-50">
                <FileText className="w-3 h-3" />读取
              </button>
            </div>

            {/* Search */}
            <div className="flex items-center gap-2 mb-3">
              <input className="flex-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                value={fsPattern} onChange={(e) => setFsPattern(e.target.value)} placeholder="搜索文件名模式 (*.txt)" />
              <button onClick={handleFsSearch} disabled={fsLoading || !fsPattern}
                className="flex items-center gap-1 px-3 py-1.5 text-xs lg:text-sm bg-violet-500/10 text-violet-500 border border-purple-500/20 rounded-lg hover:bg-violet-500/20 disabled:opacity-50">
                <Search className="w-3 h-3" />搜索
              </button>
            </div>

            {/* Results */}
            {fsResult && (
              <div className="p-2 rounded bg-muted text-xs font-mono max-h-[400px] overflow-auto">
                <pre>{JSON.stringify(fsResult.data, null, 2)}</pre>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
