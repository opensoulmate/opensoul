'use client';

import { useEffect, useState, useMemo, type ReactNode } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { AppShell, LeftPanel, WorkspacePanel, useAppStore, cn } from '@opensoulmate/openface';
import {
  Server, BookOpen, Bot, Settings, Wrench, BarChart3,
  Search, GitBranch, ScrollText, Activity, Lightbulb, Tag,
  Network, Brain, Database, GraduationCap, Sparkles, MessageSquare,
  Users, UsersRound, Zap, Plug, Cpu, Globe, Workflow, Timer,
  Heart, ShieldCheck, CircleDot, Bug, Package, Gauge,
  Hand, Eye, Mic, Camera, Volume2, ListChecks, FlaskConical,
  Link2, Radio, Download, Copy, Shield, Home, Dna,
  LayoutDashboard, Bell, FolderOpen, Terminal, Layers,
} from 'lucide-react';
import { apiFetch } from '@/lib/api';
import { useAuthStore } from '@/lib/store';
import { LoginPage } from '@/app/login-page';
import { ThemeSwitcher } from '@/app/theme-switcher';

// ── 一级导航（BottomBar）────────────────────────────────────────

const SECTIONS = [
  { id: 'knowledge', label: '知识', icon: <BookOpen className="w-full h-full" /> },
  { id: 'agent', label: 'Agent', icon: <Bot className="w-full h-full" /> },
  { id: 'system', label: '系统', icon: <Settings className="w-full h-full" /> },
  { id: 'capability', label: '能力', icon: <Wrench className="w-full h-full" /> },
  { id: 'ops', label: '运维', icon: <BarChart3 className="w-full h-full" /> },
] as const;

type SectionId = typeof SECTIONS[number]['id'];

// ── 二级导航（LeftPanel）────────────────────────────────────────

const SECTION_NAV: Record<SectionId, { path: string; label: string; icon: ReactNode }[]> = {
  knowledge: [
    { path: '/', label: '总览', icon: <LayoutDashboard className="w-4 h-4" /> },
    { path: '/knowledge', label: '知识库', icon: <BookOpen className="w-4 h-4" /> },
    { path: '/knowledge-graph', label: '知识图谱', icon: <Network className="w-4 h-4" /> },
    { path: '/brain', label: '知识大脑', icon: <Brain className="w-4 h-4" /> },
    { path: '/patterns', label: '模式发现', icon: <Lightbulb className="w-4 h-4" /> },
    { path: '/evolution', label: '进化流水线', icon: <GitBranch className="w-4 h-4" /> },
    { path: '/feedback', label: '反馈提取', icon: <MessageSquare className="w-4 h-4" /> },
    { path: '/learn', label: '学习系统', icon: <GraduationCap className="w-4 h-4" /> },
    { path: '/hippo', label: '海马体', icon: <Database className="w-4 h-4" /> },
    { path: '/mind', label: '心智', icon: <Sparkles className="w-4 h-4" /> },
    { path: '/intelligence', label: '智能分析', icon: <Brain className="w-4 h-4" /> },
    { path: '/search', label: '搜索', icon: <Search className="w-4 h-4" /> },
    { path: '/dedup', label: '知识去重', icon: <Copy className="w-4 h-4" /> },
    { path: '/tags', label: '标签', icon: <Tag className="w-4 h-4" /> },
    { path: '/entity', label: '实体', icon: <Layers className="w-4 h-4" /> },
  ],
  agent: [
    { path: '/agents', label: 'Agent管理', icon: <Bot className="w-4 h-4" /> },
    { path: '/ai-groups', label: 'AI群组', icon: <UsersRound className="w-4 h-4" /> },
    { path: '/skills', label: '技能', icon: <Zap className="w-4 h-4" /> },
    { path: '/mcp', label: 'MCP', icon: <Plug className="w-4 h-4" /> },
    { path: '/collab', label: 'Agent协作', icon: <Users className="w-4 h-4" /> },
    { path: '/ai-engine', label: 'AI引擎', icon: <Cpu className="w-4 h-4" /> },
    { path: '/agent-proxy', label: 'Agent代理', icon: <Terminal className="w-4 h-4" /> },
    { path: '/sessions', label: '会话', icon: <MessageSquare className="w-4 h-4" /> },
    { path: '/a2a', label: 'A2A协议', icon: <Globe className="w-4 h-4" /> },
    { path: '/hermes', label: 'Hermes桥接', icon: <Globe className="w-4 h-4" /> },
    { path: '/plugins', label: '插件', icon: <Plug className="w-4 h-4" /> },
    { path: '/marketplace', label: '市场', icon: <Home className="w-4 h-4" /> },
    { path: '/gene', label: '基因模板', icon: <Dna className="w-4 h-4" /> },
    { path: '/heredity', label: '遗传链', icon: <Dna className="w-4 h-4" /> },
  ],
  system: [
    { path: '/vital', label: '生命体征', icon: <Heart className="w-4 h-4" /> },
    { path: '/immune', label: '免疫系统', icon: <ShieldCheck className="w-4 h-4" /> },
    { path: '/healer', label: '自愈系统', icon: <Heart className="w-4 h-4" /> },
    { path: '/pulse', label: '脉搏', icon: <CircleDot className="w-4 h-4" /> },
    { path: '/nerve', label: '神经系统', icon: <Zap className="w-4 h-4" /> },
    { path: '/marrow', label: '骨髓备份', icon: <Database className="w-4 h-4" /> },
    { path: '/diagnostics', label: '诊断', icon: <Bug className="w-4 h-4" /> },
    { path: '/registry', label: '注册中心', icon: <Package className="w-4 h-4" /> },
    { path: '/nest', label: '多租户', icon: <Home className="w-4 h-4" /> },
    { path: '/users', label: '用户', icon: <Users className="w-4 h-4" /> },
    { path: '/permissions', label: '权限', icon: <Shield className="w-4 h-4" /> },
    { path: '/notifications', label: '通知', icon: <Bell className="w-4 h-4" /> },
    { path: '/llm', label: 'LLM配置', icon: <Cpu className="w-4 h-4" /> },
    { path: '/gland', label: '模型路由', icon: <Cpu className="w-4 h-4" /> },
    { path: '/system', label: '系统设置', icon: <Settings className="w-4 h-4" /> },
  ],
  capability: [
    { path: '/limb', label: 'RPA执行', icon: <Hand className="w-4 h-4" /> },
    { path: '/workflow', label: '工作流', icon: <ListChecks className="w-4 h-4" /> },
    { path: '/pipeline', label: '流水线', icon: <Workflow className="w-4 h-4" /> },
    { path: '/will', label: '意志系统', icon: <Workflow className="w-4 h-4" /> },
    { path: '/cortex', label: '皮层推理', icon: <Brain className="w-4 h-4" /> },
    { path: '/sense', label: '感官感知', icon: <Eye className="w-4 h-4" /> },
    { path: '/vision', label: '视觉成像', icon: <Eye className="w-4 h-4" /> },
    { path: '/voice', label: '语音', icon: <Mic className="w-4 h-4" /> },
    { path: '/capture', label: '网页捕获', icon: <Camera className="w-4 h-4" /> },
    { path: '/echo', label: '消息通道', icon: <Volume2 className="w-4 h-4" /> },
    { path: '/reflex', label: '条件反射', icon: <Zap className="w-4 h-4" /> },
    { path: '/mirror', label: '沙箱镜像', icon: <FlaskConical className="w-4 h-4" /> },
    { path: '/link', label: '连接器', icon: <Link2 className="w-4 h-4" /> },
    { path: '/chat-test', label: 'RAG问答', icon: <MessageSquare className="w-4 h-4" /> },
  ],
  ops: [
    { path: '/monitoring', label: '监控面板', icon: <Activity className="w-4 h-4" /> },
    { path: '/topology', label: '系统拓扑', icon: <Network className="w-4 h-4" /> },
    { path: '/timeline', label: '事件时间线', icon: <Activity className="w-4 h-4" /> },
    { path: '/events', label: '事件流', icon: <Radio className="w-4 h-4" /> },
    { path: '/traces', label: '执行轨迹', icon: <ScrollText className="w-4 h-4" /> },
    { path: '/trajectory', label: '轨迹管理', icon: <Activity className="w-4 h-4" /> },
    { path: '/benchmark', label: '基准测试', icon: <Gauge className="w-4 h-4" /> },
    { path: '/logs', label: '日志', icon: <ScrollText className="w-4 h-4" /> },
    { path: '/cron', label: '定时任务', icon: <Timer className="w-4 h-4" /> },
    { path: '/admin-actions', label: '运维操作', icon: <Wrench className="w-4 h-4" /> },
    { path: '/workspace', label: '工作空间', icon: <FolderOpen className="w-4 h-4" /> },
    { path: '/git', label: 'Git', icon: <GitBranch className="w-4 h-4" /> },
    { path: '/vein', label: '文件存储', icon: <Database className="w-4 h-4" /> },
    { path: '/export', label: '数据导出', icon: <Download className="w-4 h-4" /> },
    { path: '/downloader', label: '下载管理', icon: <Download className="w-4 h-4" /> },
  ],
};

// ── 当前所在区域 ────────────────────────────────────────────────

function getActiveSection(pathname: string): SectionId {
  for (const [sectionId, items] of Object.entries(SECTION_NAV)) {
    for (const item of items) {
      if (item.path === '/' ? pathname === '/' : pathname.startsWith(item.path)) {
        return sectionId as SectionId;
      }
    }
  }
  return 'system'; // fallback
}

// ── LeftPanel 二级导航 ──────────────────────────────────────────

function SectionNavList({ section, activePath, onNavigate }: { section: SectionId; activePath: string; onNavigate: (path: string) => void }) {
  const items = SECTION_NAV[section];
  return (
    <div className="flex flex-col gap-0.5 p-2 pt-3">
      <div className="px-3 py-1.5 text-[10px] font-semibold uppercase text-muted-foreground/60 tracking-wider">
        {SECTIONS.find(s => s.id === section)?.label}
      </div>
      {items.map((item) => {
        const active = item.path === '/' ? activePath === '/' : activePath.startsWith(item.path);
        return (
          <button
            key={item.path}
            onClick={() => onNavigate(item.path)}
            className={cn(
              'flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors',
              active ? 'bg-primary/10 text-primary font-medium' : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
            )}
          >
            <span className="shrink-0">{item.icon}</span>
            <span className="truncate">{item.label}</span>
          </button>
        );
      })}
    </div>
  );
}

// ── Admin Shell ─────────────────────────────────────────────────

export function AdminShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const logout = useAuthStore((s) => s.logout);
  const hydrate = useAuthStore((s) => s.hydrate);
  const setRightPanelOpen = useAppStore((s) => s.setRightPanelOpen);
  const [ready, setReady] = useState(false);

  const activeSection = getActiveSection(pathname);

  // BottomBar一级导航：5个区域
  const bottomNav = useMemo(() => SECTIONS.map(s => ({
    path: `/__section__/${s.id}`, // 伪路径，不实际导航
    label: s.label,
    icon: s.icon,
  })), []);

  useEffect(() => { hydrate(); setReady(true); }, [hydrate]);
  useEffect(() => { setRightPanelOpen(false); }, [pathname, setRightPanelOpen]);

  if (!ready) return <div className="flex items-center justify-center min-h-screen bg-background"><div className="text-sm text-muted-foreground">加载中...</div></div>;
  if (!isAuthenticated) return <LoginPage />;

  // 当前页面标题
  const currentPage = Object.values(SECTION_NAV).flat().find(item =>
    item.path === '/' ? pathname === '/' : pathname.startsWith(item.path)
  );

  return (
    <AppShell
      appName="OpenSoul"
      logo={<Server className="w-full h-full" />}
      nav={bottomNav}
      activePath={`/__section__/${activeSection}`}
      onNavigate={(path) => {
        // 点击BottomBar区域图标 → 跳转到该区域的第一个页面
        const sectionId = path.replace('/__section__/', '') as SectionId;
        const firstItem = SECTION_NAV[sectionId]?.[0];
        if (firstItem) router.push(firstItem.path);
      }}
      onSettings={() => setRightPanelOpen(true)}
      userName="admin"
      onLogout={() => { logout(); router.push('/'); }}
      leftPanel={
        <LeftPanel
          renderContent={() => (
            <SectionNavList
              section={activeSection}
              activePath={pathname}
              onNavigate={(p) => router.push(p)}
            />
          )}
          placeholder="搜索功能..."
        />
      }
      rightPanel={
        <WorkspacePanel
          sessionId="admin-main"
          workspaceTitle="OpenSoul Workspace"
        />
      }
      bottomRight={
        <div className="flex items-center gap-1 pr-2">
          <ThemeSwitcher />
          <span className="text-[10px] text-muted-foreground px-2">v0.1.0</span>
        </div>
      }
      mainPanelProps={{
        title: currentPage?.label || '总览',
        icon: currentPage?.icon,
      }}
    >
      <div className="flex-1 overflow-auto p-4 lg:p-6">
        {children}
      </div>
    </AppShell>
  );
}
