'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, RefreshCw, X, Monitor, Terminal, Wifi, Plug,
  Cpu, HardDrive, MemoryStick, Activity, Clock, Globe,
  Server, Database, FolderOpen, ChevronDown, ChevronRight,
  AlertTriangle, Loader2, Zap, Settings, Eye,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

// ── Types ───────────────────────────────────────────────────

interface ProcessInfo {
  pid: number;
  user: string;
  cpu: number;
  mem: number;
  command: string;
  executable: string | null;
  description: string;
}

interface CLITool {
  name: string;
  path: string;
  version: string | null;
  description: string;
}

interface ServiceInfo {
  protocol: string;
  local_address: string;
  local_port: number;
  state: string;
  process_name: string | null;
  pid: number | null;
  description: string;
}

interface ScanResult {
  scan_time: number;
  processes: ProcessInfo[];
  cli_tools: CLITool[];
  services: ServiceInfo[];
  adapters: string[];
}

interface AdapterInfo {
  name: string;
  description: string;
  registered_at: number;
}

// ── Helpers ─────────────────────────────────────────────────

const TABS = [
  { id: 'overview', label: '总览', icon: Monitor },
  { id: 'processes', label: '进程', icon: Cpu },
  { id: 'tools', label: 'CLI工具', icon: Terminal },
  { id: 'services', label: '网络服务', icon: Wifi },
  { id: 'adapters', label: '适配器', icon: Plug },
] as const;

type TabId = typeof TABS[number]['id'];

function formatTime(seconds: number) {
  return `${(seconds * 1000).toFixed(0)}ms`;
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / 1048576).toFixed(1)}MB`;
}

// ── Overview Cards ──────────────────────────────────────────

function OverviewCards({ data }: { data: ScanResult }) {
  const highCpuProcs = data.processes.filter(p => p.cpu > 5).length;
  const highMemProcs = data.processes.filter(p => p.mem > 5).length;
  const listeningServices = data.services.filter(s => s.state === 'LISTEN').length;
  const tcpServices = data.services.filter(s => s.protocol === 'tcp').length;

  return (
    <div className="space-y-4">
      {/* Stats row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <Cpu className="w-3.5 h-3.5 text-blue-500" />
            <span className="text-xs text-muted-foreground">进程</span>
          </div>
          <div className="text-2xl font-bold">{data.processes.length}</div>
          {highCpuProcs > 0 && (
            <div className="text-[10px] text-amber-500 mt-0.5">{highCpuProcs} 个高CPU (&gt;5%)</div>
          )}
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <Terminal className="w-3.5 h-3.5 text-emerald-500" />
            <span className="text-xs text-muted-foreground">CLI工具</span>
          </div>
          <div className="text-2xl font-bold">{data.cli_tools.length}</div>
          <div className="text-[10px] text-muted-foreground mt-0.5">已安装在PATH</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <Wifi className="w-3.5 h-3.5 text-violet-500" />
            <span className="text-xs text-muted-foreground">网络服务</span>
          </div>
          <div className="text-2xl font-bold">{data.services.length}</div>
          <div className="text-[10px] text-green-500 mt-0.5">{listeningServices} 监听中</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <Plug className="w-3.5 h-3.5 text-amber-500" />
            <span className="text-xs text-muted-foreground">适配器</span>
          </div>
          <div className="text-2xl font-bold">{data.adapters.length}</div>
          <div className="text-[10px] text-muted-foreground mt-0.5">已注册</div>
        </div>
      </div>

      {/* Scan info */}
      <div className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-center gap-2 mb-3">
          <Activity className="w-4 h-4" />
          <span className="text-sm font-medium">扫描概要</span>
          <span className="text-[10px] text-muted-foreground ml-auto">
            耗时 {formatTime(data.scan_time)}
          </span>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 text-xs">
          <div className="rounded bg-muted/50 p-2.5">
            <div className="text-muted-foreground text-[10px]">高内存进程</div>
            <div className="text-lg font-bold text-amber-500">{highMemProcs}</div>
          </div>
          <div className="rounded bg-muted/50 p-2.5">
            <div className="text-muted-foreground text-[10px]">TCP连接</div>
            <div className="text-lg font-bold">{tcpServices}</div>
          </div>
          <div className="rounded bg-muted/50 p-2.5">
            <div className="text-muted-foreground text-[10px]">版本化工具</div>
            <div className="text-lg font-bold text-green-500">
              {data.cli_tools.filter(t => t.version).length}
            </div>
          </div>
        </div>
      </div>

      {/* Top processes by CPU */}
      <div className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-center gap-2 mb-3">
          <Cpu className="w-4 h-4 text-blue-500" />
          <span className="text-sm font-medium">Top 进程 (CPU)</span>
        </div>
        <div className="space-y-1.5">
          {[...data.processes].sort((a, b) => b.cpu - a.cpu).slice(0, 8).map((p, i) => (
            <div key={i} className="flex items-center gap-2 py-1 px-2 rounded hover:bg-muted/30">
              <span className="text-[10px] text-muted-foreground w-5 text-right font-mono">{p.pid}</span>
              <div className="flex-1 min-w-0">
                <div className="text-xs font-medium truncate">{p.executable || p.command.split(' ')[0]}</div>
                <div className="text-[10px] text-muted-foreground truncate">{p.command}</div>
              </div>
              <div className="flex items-center gap-3 shrink-0">
                <span className={`text-xs font-mono ${p.cpu > 10 ? 'text-red-500' : p.cpu > 5 ? 'text-amber-500' : 'text-muted-foreground'}`}>
                  {p.cpu.toFixed(1)}%
                </span>
                <span className="text-xs font-mono text-muted-foreground w-12 text-right">
                  {p.mem.toFixed(1)}%
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Listening services */}
      <div className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-center gap-2 mb-3">
          <Wifi className="w-4 h-4 text-violet-500" />
          <span className="text-sm font-medium">监听端口</span>
        </div>
        <div className="space-y-1">
          {data.services.filter(s => s.state === 'LISTEN').sort((a, b) => a.local_port - b.local_port).slice(0, 12).map((s, i) => (
            <div key={i} className="flex items-center gap-2 py-1 px-2 rounded hover:bg-muted/30">
              <Badge variant="bare" className={`${s.protocol === 'tcp' ? 'bg-blue-500/10 text-blue-500' : 'bg-violet-500/10 text-violet-500'}`}>
                {s.protocol.toUpperCase()}
              </Badge>
              <span className="text-xs font-mono">{s.local_address}:{s.local_port}</span>
              {s.process_name && (
                <span className="text-[10px] text-muted-foreground truncate flex-1">{s.process_name}</span>
              )}
              {s.pid && (
                <span className="text-[10px] text-muted-foreground font-mono">PID:{s.pid}</span>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Processes Tab ───────────────────────────────────────────

function ProcessesTab({ processes }: { processes: ProcessInfo[] }) {
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState<'cpu' | 'mem' | 'pid'>('cpu');

  const filtered = processes
    .filter(p => {
      if (!search) return true;
      const q = search.toLowerCase();
      return p.command.toLowerCase().includes(q) ||
        (p.executable || '').toLowerCase().includes(q) ||
        String(p.pid).includes(q) ||
        p.user.toLowerCase().includes(q);
    })
    .sort((a, b) => {
      if (sortBy === 'cpu') return b.cpu - a.cpu;
      if (sortBy === 'mem') return b.mem - a.mem;
      return a.pid - b.pid;
    });

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索进程名、PID、用户..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value as any)}
        >
          <option value="cpu">按CPU排序</option>
          <option value="mem">按内存排序</option>
          <option value="pid">按PID排序</option>
        </select>
      </div>

      <div className="text-xs text-muted-foreground">共 {filtered.length} 个进程</div>

      <div className="space-y-1">
        {filtered.map((p, i) => (
          <div key={`${p.pid}-${i}`} className="flex items-center gap-2 py-2 px-3 rounded-lg border border-border bg-card hover:bg-muted/30 transition-colors">
            <div className="w-12 text-right">
              <span className="text-xs font-mono text-muted-foreground">{p.pid}</span>
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium truncate">{p.executable || p.command.split(' ')[0]}</span>
                <Badge variant="muted">{p.user}</Badge>
              </div>
              <p className="text-[10px] text-muted-foreground truncate mt-0.5">{p.command}</p>
            </div>
            <div className="flex items-center gap-4 shrink-0">
              <div className="text-right">
                <div className={`text-xs font-mono ${p.cpu > 10 ? 'text-red-500 font-bold' : p.cpu > 5 ? 'text-amber-500' : ''}`}>
                  CPU {p.cpu.toFixed(1)}%
                </div>
                <div className="w-16 h-1.5 bg-muted rounded-full mt-1 overflow-hidden">
                  <div className={`h-full rounded-full ${p.cpu > 10 ? 'bg-red-500' : p.cpu > 5 ? 'bg-amber-500' : 'bg-blue-500'}`} style={{ width: `${Math.min(100, p.cpu)}%` }} />
                </div>
              </div>
              <div className="text-right">
                <div className="text-xs font-mono text-muted-foreground">MEM {p.mem.toFixed(1)}%</div>
                <div className="w-16 h-1.5 bg-muted rounded-full mt-1 overflow-hidden">
                  <div className="h-full rounded-full bg-purple-500" style={{ width: `${Math.min(100, p.mem)}%` }} />
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>

      {filtered.length === 0 && (
        <div className="text-center py-8 text-muted-foreground text-xs">无匹配进程</div>
      )}
    </div>
  );
}

// ── CLI Tools Tab ───────────────────────────────────────────

function ToolsTab({ tools }: { tools: CLITool[] }) {
  const [search, setSearch] = useState('');

  const filtered = tools.filter(t => {
    if (!search) return true;
    const q = search.toLowerCase();
    return t.name.toLowerCase().includes(q) || (t.description || '').toLowerCase().includes(q);
  });

  // Group by category
  const categories: Record<string, CLITool[]> = {};
  filtered.forEach(t => {
    const cat = t.description?.includes('compiler') || t.description?.includes('Compiler') ? '编译器' :
      t.description?.includes('package') || t.description?.includes('Package') ? '包管理' :
      t.description?.includes('container') || t.description?.includes('Container') ? '容器' :
      t.description?.includes('database') || t.description?.includes('Database') || t.description?.includes('SQL') ? '数据库' :
      t.description?.includes('editor') || t.description?.includes('Editor') ? '编辑器' :
      '其他';
    if (!categories[cat]) categories[cat] = [];
    categories[cat].push(t);
  });

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索工具名称..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <span className="text-xs text-muted-foreground">{filtered.length} 个工具</span>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
        {filtered.map((tool) => (
          <div key={tool.name} className="flex items-center gap-3 p-2.5 rounded-lg border border-border bg-card hover:bg-muted/30 transition-colors">
            <div className="w-8 h-8 rounded-lg bg-emerald-500/10 flex items-center justify-center shrink-0">
              <Terminal className="w-4 h-4 text-emerald-500" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <span className="text-sm font-medium font-mono">{tool.name}</span>
                {tool.version && (
                  <Badge variant="default">{tool.version}</Badge>
                )}
              </div>
              <p className="text-[10px] text-muted-foreground truncate">{tool.description || tool.path}</p>
            </div>
          </div>
        ))}
      </div>

      {filtered.length === 0 && (
        <div className="text-center py-8 text-muted-foreground text-xs">无匹配工具</div>
      )}
    </div>
  );
}

// ── Services Tab ────────────────────────────────────────────

function ServicesTab({ services }: { services: ServiceInfo[] }) {
  const [search, setSearch] = useState('');
  const [filterState, setFilterState] = useState('all');

  const states = Array.from(new Set(services.map(s => s.state))).sort();

  const filtered = services.filter(s => {
    if (filterState !== 'all' && s.state !== filterState) return false;
    if (search) {
      const q = search.toLowerCase();
      return s.local_address.includes(q) ||
        String(s.local_port).includes(q) ||
        (s.process_name || '').toLowerCase().includes(q) ||
        s.protocol.includes(q);
    }
    return true;
  }).sort((a, b) => a.local_port - b.local_port);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索地址、端口、进程..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
          value={filterState}
          onChange={(e) => setFilterState(e.target.value)}
        >
          <option value="all">全部状态</option>
          {states.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <span className="text-xs text-muted-foreground">{filtered.length} 个服务</span>
      </div>

      <div className="space-y-1">
        {filtered.map((s, i) => (
          <div key={`${s.local_address}-${s.local_port}-${i}`} className="flex items-center gap-3 py-2 px-3 rounded-lg border border-border bg-card hover:bg-muted/30 transition-colors">
            <Badge variant="bare" className={`${
 s.protocol === 'tcp' ? 'bg-blue-500/10 text-blue-500 border border-blue-500/30' :
 'bg-violet-500/10 text-violet-500 border border-violet-500/30'
 }`}>
              {s.protocol.toUpperCase()}
            </Badge>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-sm font-mono">{s.local_address}:{s.local_port}</span>
                <Badge variant="bare" className={`${
 s.state === 'LISTEN' ? 'bg-emerald-500/10 text-emerald-500' :
 s.state === 'ESTAB' ? 'bg-blue-500/10 text-blue-500' :
 'bg-muted text-muted-foreground'
 }`}>
                  {s.state}
                </Badge>
              </div>
              {s.description && <p className="text-[10px] text-muted-foreground mt-0.5">{s.description}</p>}
            </div>
            <div className="text-right shrink-0">
              {s.process_name && <div className="text-xs">{s.process_name}</div>}
              {s.pid && <div className="text-[10px] text-muted-foreground font-mono">PID: {s.pid}</div>}
            </div>
          </div>
        ))}
      </div>

      {filtered.length === 0 && (
        <div className="text-center py-8 text-muted-foreground text-xs">无匹配服务</div>
      )}
    </div>
  );
}

// ── Adapters Tab ────────────────────────────────────────────

function AdaptersTab({ adapters }: { adapters: string[] }) {
  const adapterIcons: Record<string, typeof Plug> = {
    rest: Globe,
    database: Database,
    filesystem: FolderOpen,
    cli: Terminal,
  };

  const adapterLabels: Record<string, string> = {
    rest: 'REST API',
    database: '数据库',
    filesystem: '文件系统',
    cli: 'CLI工具',
  };

  return (
    <div className="space-y-3">
      <div className="text-xs text-muted-foreground">已注册 {adapters.length} 个适配器</div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {adapters.map((name) => {
          const Icon = adapterIcons[name] || Plug;
          return (
            <div key={name} className="flex items-center gap-3 p-4 rounded-lg border border-border bg-card">
              <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center">
                <Icon className="w-5 h-5 text-primary" />
              </div>
              <div>
                <div className="text-sm font-medium">{adapterLabels[name] || name}</div>
                <div className="text-[10px] text-muted-foreground font-mono">{name}</div>
              </div>
              <div className="ml-auto">
                <Badge variant="success" className="border border-emerald-500/30">
                  已注册
                </Badge>
              </div>
            </div>
          );
        })}
      </div>

      {adapters.length === 0 && (
        <div className="text-center py-8 text-muted-foreground">
          <Plug className="w-10 h-10 mx-auto mb-2 opacity-30" />
          <p className="text-xs">暂无注册适配器</p>
          <p className="text-[10px] mt-1">适配器通过 Soma Connector 注册</p>
        </div>
      )}
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────

export default function SomaPage() {
  const [data, setData] = useState<ScanResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<TabId>('overview');
  const [scanning, setScanning] = useState(false);

  const fetchScan = useCallback(async () => {
    try {
      setLoading(true);
      setError('');
      const result = await apiFetch('/api/soma/discovery/scan');
      setData(result);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchScan(); }, [fetchScan]);

  const handleRescan = async () => {
    try {
      setScanning(true);
      // Force rescan via POST
      const result = await apiFetch('/api/soma/discovery/scan', { method: 'POST' });
      setData(result);
    } catch {
      // Fallback: just re-fetch
      await fetchScan();
    } finally {
      setScanning(false);
    }
  };

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-1 bg-muted rounded-lg p-0.5">
          {TABS.map((t) => {
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
        <div className="ml-auto flex items-center gap-2">
          {data && (
            <span className="text-[10px] text-muted-foreground">
              扫描耗时 {formatTime(data.scan_time)}
            </span>
          )}
          <Button onClick={handleRescan}
            disabled={scanning} variant="primary" className="gap-1.5">
            {scanning ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
            {scanning ? '扫描中...' : '重新扫描'}
          </Button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-red-500 shrink-0" />
          <span className="text-xs text-red-500">{error}</span>
          <Button onClick={() => setError('')} variant="icon-sm" className="ml-auto hover:bg-muted">
            <X className="w-3 h-3" />
          </Button>
        </div>
      )}

      {/* Content */}
      {data && (
        <>
          {tab === 'overview' && <OverviewCards data={data} />}
          {tab === 'processes' && <ProcessesTab processes={data.processes} />}
          {tab === 'tools' && <ToolsTab tools={data.cli_tools} />}
          {tab === 'services' && <ServicesTab services={data.services} />}
          {tab === 'adapters' && <AdaptersTab adapters={data.adapters} />}
        </>
      )}

      {data && tab === 'overview' && (
        <div className="text-[10px] text-muted-foreground text-center">
          Soma Discovery · 本地系统感知层 · 进程/工具/服务/适配器
        </div>
      )}
    </div>
  );
}
