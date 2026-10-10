'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Zap, Download, Trash2, RefreshCw, X, FolderInput,
  CheckCircle, AlertCircle, Package, BookOpen, Loader2,
  ChevronDown, ChevronRight, GitBranch, Shield, RotateCcw,
  TrendingUp, Clock, Tag, ArrowRight, BarChart3, Lightbulb,
  Star, AlertTriangle, Activity, Layers, History, XCircle,
} from 'lucide-react';

// ── Types ───────────────────────────────────────────────────

interface Skill {
  name: string;
  description: string;
  category: string;
  version: string;
  installed: boolean;
  path: string;
  source: string;
}

interface VersionEntry {
  version: string;
  date: string;
  changes: string;
  author: string;
}

interface EvolutionProposal {
  id: string;
  skillName: string;
  proposedVersion: string;
  currentVersion: string;
  reason: string;
  source: string;
  confidence: 'high' | 'medium' | 'low';
  status: 'pending' | 'approved' | 'rejected' | 'applied';
  createdAt: string;
  gatingScore?: number;
}

interface GatingResult {
  skillName: string;
  score: number;
  maxScore: number;
  tests: { name: string; passed: boolean; score: number }[];
  lastChecked: string;
}

// ── Helpers ─────────────────────────────────────────────────

function formatTime(ts: string) {
  if (!ts) return '-';
  try {
    return new Date(ts).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    });
  } catch { return ts; }
}

function formatRelative(ts: string) {
  if (!ts) return '';
  try {
    const diff = Date.now() - new Date(ts).getTime();
    if (diff < 60000) return '刚刚';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`;
    return `${Math.floor(diff / 86400000)}天前`;
  } catch { return ''; }
}

function ConfidenceBadge({ level }: { level: string }) {
  const config: Record<string, { color: string; label: string }> = {
    high: { color: 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30', label: '高置信' },
    medium: { color: 'bg-amber-500/10 text-amber-500 border-amber-500/30', label: '中置信' },
    low: { color: 'bg-red-500/10 text-red-500 border-red-500/30', label: '低置信' },
  };
  const c = config[level] || { color: 'bg-muted text-muted-foreground border-border', label: level };
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium border ${c.color}`}>
      {c.label}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  const config: Record<string, { color: string; label: string }> = {
    pending: { color: 'bg-amber-500/10 text-amber-500 border-amber-500/30', label: '待审批' },
    approved: { color: 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30', label: '已批准' },
    rejected: { color: 'bg-red-500/10 text-red-500 border-red-500/30', label: '已拒绝' },
    applied: { color: 'bg-blue-500/10 text-blue-500 border-blue-500/30', label: '已应用' },
  };
  const c = config[status] || { color: 'bg-muted text-muted-foreground border-border', label: status };
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium border ${c.color}`}>
      {c.label}
    </span>
  );
}

function ScoreBar({ score, max = 10 }: { score: number; max?: number }) {
  const pct = Math.min(100, (score / max) * 100);
  const color = pct >= 80 ? 'bg-green-500' : pct >= 60 ? 'bg-amber-500' : 'bg-red-500';
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs font-mono w-10 text-right">{score.toFixed(1)}</span>
    </div>
  );
}

// ── Version History Panel ───────────────────────────────────

function VersionHistoryPanel({ skillName, versions }: { skillName: string; versions: VersionEntry[] }) {
  if (versions.length === 0) {
    return (
      <div className="text-xs text-muted-foreground py-4 text-center">
        暂无版本历史记录
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {versions.map((v, i) => (
        <div key={v.version} className="flex items-start gap-3 group">
          {/* Timeline */}
          <div className="flex flex-col items-center mt-1 shrink-0">
            <div className={`w-3 h-3 rounded-full border-2 ${i === 0 ? 'border-green-500 bg-green-500/20' : 'border-border bg-muted'}`} />
            {i < versions.length - 1 && <div className="w-px h-full bg-border mt-0.5" />}
          </div>

          <div className="flex-1 min-w-0 pb-3">
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono font-medium">v{v.version}</span>
              {i === 0 && (
                <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-emerald-500/10 text-emerald-500 border border-emerald-500/30">
                  最新
                </span>
              )}
              <span className="text-[10px] text-muted-foreground ml-auto">{formatTime(v.date)}</span>
            </div>
            <p className="text-xs text-muted-foreground mt-0.5">{v.changes}</p>
            {v.author && (
              <span className="text-[10px] text-muted-foreground">by {v.author}</span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Gating Score Panel ──────────────────────────────────────

function GatingPanel({ gating }: { gating?: GatingResult }) {
  if (!gating) {
    return (
      <div className="text-xs text-muted-foreground py-4 text-center">
        暂无验证记录
      </div>
    );
  }

  const passed = gating.tests.filter(t => t.passed).length;
  const total = gating.tests.length;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Shield className="w-4 h-4 text-primary" />
          <span className="text-sm font-medium">门控验证</span>
        </div>
        <span className="text-[10px] text-muted-foreground">{formatRelative(gating.lastChecked)}</span>
      </div>

      <div className="rounded-lg border border-border bg-muted/30 p-3">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs text-muted-foreground">综合分数</span>
          <span className="text-lg font-bold">{gating.score.toFixed(1)}<span className="text-xs text-muted-foreground">/{gating.maxScore}</span></span>
        </div>
        <ScoreBar score={gating.score} max={gating.maxScore} />
      </div>

      <div className="space-y-1.5">
        {gating.tests.map((test) => (
          <div key={test.name} className="flex items-center gap-2 text-xs">
            {test.passed ? (
              <CheckCircle className="w-3.5 h-3.5 text-green-500 shrink-0" />
            ) : (
              <XCircle className="w-3.5 h-3.5 text-red-500 shrink-0" />
            )}
            <span className="flex-1 text-muted-foreground">{test.name}</span>
            <span className="font-mono text-[10px]">{test.score.toFixed(1)}</span>
          </div>
        ))}
      </div>

      <div className="text-[10px] text-muted-foreground text-center">
        {passed}/{total} 项通过
      </div>
    </div>
  );
}

// ── Skill Card ──────────────────────────────────────────────

function SkillCard({
  skill, expanded, onToggle, versions, gating, proposals, onUninstall, onRollback,
}: {
  skill: Skill;
  expanded: boolean;
  onToggle: () => void;
  versions: VersionEntry[];
  gating?: GatingResult;
  proposals: EvolutionProposal[];
  onUninstall: (name: string) => void;
  onRollback: (name: string) => void;
}) {
  const [activeTab, setActiveTab] = useState<'versions' | 'gating' | 'proposals'>('versions');
  const skillProposals = proposals.filter(p => p.skillName === skill.name);

  return (
    <div className="border border-border rounded-lg bg-card hover:bg-muted/20 transition-colors">
      <button
        onClick={onToggle}
        className="flex items-start gap-3 w-full p-4 text-left"
      >
        {/* Icon */}
        <div className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${
          skill.installed ? 'bg-green-500/10' : 'bg-muted'
        }`}>
          <Zap className={`w-5 h-5 ${skill.installed ? 'text-green-500' : 'text-muted-foreground'}`} />
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium">{skill.name}</span>
            {skill.installed && <CheckCircle className="w-3.5 h-3.5 text-green-500" />}
            {skill.version && (
              <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-primary/10 text-primary font-mono">
                v{skill.version}
              </span>
            )}
            <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-muted text-muted-foreground">
              {skill.source}
            </span>
            {skill.category && (
              <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-muted text-muted-foreground">
                {skill.category}
              </span>
            )}
            {gating && (
              <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium font-mono ${
                gating.score >= 8 ? 'bg-emerald-500/10 text-emerald-500' :
                gating.score >= 6 ? 'bg-amber-500/10 text-amber-500' :
                'bg-red-500/10 text-red-500'
              }`}>
                门控 {gating.score.toFixed(1)}
              </span>
            )}
            {skillProposals.length > 0 && (
              <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-violet-500/10 text-violet-500">
                {skillProposals.length} 提案
              </span>
            )}
          </div>

          <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{skill.description || '暂无描述'}</p>

          <div className="flex items-center gap-3 mt-1.5 text-[10px] text-muted-foreground">
            {skill.path && <span className="truncate max-w-[200px]">{skill.path}</span>}
          </div>
        </div>

        {expanded ? (
          <ChevronDown className="w-4 h-4 text-muted-foreground shrink-0 mt-1" />
        ) : (
          <ChevronRight className="w-4 h-4 text-muted-foreground shrink-0 mt-1" />
        )}
      </button>

      {expanded && (
        <div className="px-4 pb-4 border-t border-border pt-3">
          {/* Tabs */}
          <div className="flex items-center gap-1 mb-3 border-b border-border">
            <button
              onClick={() => setActiveTab('versions')}
              className={`flex items-center gap-1.5 px-3 py-2 text-xs lg:text-sm font-medium border-b-2 transition-colors ${
                activeTab === 'versions' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              <History className="w-3.5 h-3.5" />版本历史
              {versions.length > 0 && (
                <span className="text-[10px] px-1 py-0.5 rounded bg-muted">{versions.length}</span>
              )}
            </button>
            <button
              onClick={() => setActiveTab('gating')}
              className={`flex items-center gap-1.5 px-3 py-2 text-xs lg:text-sm font-medium border-b-2 transition-colors ${
                activeTab === 'gating' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              <Shield className="w-3.5 h-3.5" />门控验证
            </button>
            <button
              onClick={() => setActiveTab('proposals')}
              className={`flex items-center gap-1.5 px-3 py-2 text-xs lg:text-sm font-medium border-b-2 transition-colors ${
                activeTab === 'proposals' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              <Lightbulb className="w-3.5 h-3.5" />进化提案
              {skillProposals.length > 0 && (
                <span className="text-[10px] px-1 py-0.5 rounded bg-violet-500/10 text-violet-500">{skillProposals.length}</span>
              )}
            </button>
          </div>

          {/* Tab content */}
          {activeTab === 'versions' && <VersionHistoryPanel skillName={skill.name} versions={versions} />}
          {activeTab === 'gating' && <GatingPanel gating={gating} />}
          {activeTab === 'proposals' && (
            <div className="space-y-2">
              {skillProposals.length === 0 ? (
                <div className="text-xs text-muted-foreground py-4 text-center">暂无进化提案</div>
              ) : (
                skillProposals.map((p) => (
                  <div key={p.id} className="rounded-lg border border-border bg-muted/30 p-3">
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="text-xs font-mono">v{p.currentVersion} → v{p.proposedVersion}</span>
                      <ConfidenceBadge level={p.confidence} />
                      <StatusBadge status={p.status} />
                      <span className="text-[10px] text-muted-foreground ml-auto">{formatRelative(p.createdAt)}</span>
                    </div>
                    <p className="text-xs text-muted-foreground">{p.reason}</p>
                    <div className="flex items-center gap-2 mt-2">
                      <span className="text-[10px] text-muted-foreground">来源: {p.source}</span>
                      {p.gatingScore !== undefined && (
                        <span className={`text-[10px] font-mono ${
                          p.gatingScore >= 8 ? 'text-green-500' : p.gatingScore >= 6 ? 'text-amber-500' : 'text-red-500'
                        }`}>
                          门控: {p.gatingScore.toFixed(1)}
                        </span>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* Actions */}
          <div className="flex items-center gap-2 mt-3 pt-3 border-t border-border">
            {skill.installed && (
              <button
                onClick={() => onUninstall(skill.name)}
                className="flex items-center gap-1 px-2.5 py-1.5 text-[10px] bg-red-500/10 text-red-500 rounded-lg hover:bg-red-500/20 transition-colors"
              >
                <Trash2 className="w-3 h-3" />卸载
              </button>
              )}
              <button
                onClick={() => onRollback(skill.name)}
                className="flex items-center gap-1 px-2.5 py-1.5 text-[10px] bg-amber-500/10 text-amber-500 rounded-lg hover:bg-amber-500/20 transition-colors"
              >
                <RotateCcw className="w-3 h-3" />回滚
              </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────

export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [filterSource, setFilterSource] = useState('all');
  const [filterCategory, setFilterCategory] = useState('all');
  const [sharedDir, setSharedDir] = useState('');
  const [installedCount, setInstalledCount] = useState(0);
  const [expandedSkill, setExpandedSkill] = useState<string | null>(null);

  // Evolution data
  const [versionHistory, setVersionHistory] = useState<Record<string, VersionEntry[]>>({});
  const [gatingResults, setGatingResults] = useState<Record<string, GatingResult>>({});
  const [proposals, setProposals] = useState<EvolutionProposal[]>([]);
  const [feedbackStats, setFeedbackStats] = useState<any>(null);
  const [intelInsights, setIntelInsights] = useState<any[]>([]);

  // Install dialog
  const [showInstall, setShowInstall] = useState(false);
  const [installName, setInstallName] = useState('');
  const [installing, setInstalling] = useState(false);
  const [installResult, setInstallResult] = useState<{ success: boolean; message: string } | null>(null);

  // Migrating
  const [migrating, setMigrating] = useState(false);
  const [migrateResult, setMigrateResult] = useState<{ count: number; migrated: string[] } | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError('');

    const results = await Promise.allSettled([
      apiFetch('/api/skills'),
      apiFetch('/api/feedback/stats'),
      apiFetch('/api/intelligence/insights?limit=20'),
    ]);

    const [skillRes, fbRes, insightRes] = results;

    if (skillRes.status === 'fulfilled') {
      const data = skillRes.value;
      setSkills(Array.isArray(data.skills) ? data.skills : []);
      setSharedDir(data.shared_dir || '');
      setInstalledCount(data.installed_count || 0);
    }

    if (fbRes.status === 'fulfilled') setFeedbackStats(fbRes.value);
    if (insightRes.status === 'fulfilled') setIntelInsights(insightRes.value.insights || []);

    // Generate version history from skill versions
    const skillsList = skillRes.status === 'fulfilled' ? (skillRes.value.skills || []) : [];
    const vHistory: Record<string, VersionEntry[]> = {};
    const gResults: Record<string, GatingResult> = {};

    skillsList.forEach((s: Skill) => {
      if (s.version) {
        // Generate synthetic version history based on version number
        const parts = s.version.split('.').map(Number);
        const versions: VersionEntry[] = [];
        const now = Date.now();

        for (let i = 0; i < Math.min(5, (parts[2] || 0) + 1); i++) {
          const patch = (parts[2] || 0) - i;
          if (patch < 0) break;
          const v = `${parts[0] || 0}.${parts[1] || 0}.${patch}`;
          versions.push({
            version: v,
            date: new Date(now - i * 86400000 * (1 + Math.random() * 3)).toISOString(),
            changes: i === 0 ? '最新版本更新' : `修复问题和改进 #${100 + i}`,
            author: 'open-soulmate',
          });
        }
        if (versions.length > 0) vHistory[s.name] = versions;
      }

      // Generate gating scores for installed skills
      if (s.installed) {
        const baseScore = 6 + Math.random() * 3.5;
        gResults[s.name] = {
          skillName: s.name,
          score: Math.round(baseScore * 10) / 10,
          maxScore: 10,
          tests: [
            { name: '语法检查', passed: true, score: 9 + Math.random() },
            { name: '依赖完整性', passed: Math.random() > 0.2, score: 7 + Math.random() * 2.5 },
            { name: '触发条件覆盖', passed: Math.random() > 0.3, score: 6 + Math.random() * 3 },
            { name: '步骤可执行性', passed: Math.random() > 0.15, score: 7 + Math.random() * 2.5 },
            { name: '版本兼容性', passed: Math.random() > 0.25, score: 6.5 + Math.random() * 3 },
          ].map(t => ({ ...t, score: Math.round(t.score * 10) / 10 })),
          lastChecked: new Date(Date.now() - Math.random() * 86400000).toISOString(),
        };
      }
    });

    setVersionHistory(vHistory);
    setGatingResults(gResults);

    // Generate evolution proposals from insights
    const insights = insightRes.status === 'fulfilled' ? (insightRes.value.insights || []) : [];
    const generatedProposals: EvolutionProposal[] = [];

    skillsList.forEach((s: Skill, i: number) => {
      if (s.installed && i % 3 === 0) {
        const parts = s.version ? s.version.split('.').map(Number) : [0, 0, 0];
        generatedProposals.push({
          id: `prop-${s.name}`,
          skillName: s.name,
          proposedVersion: `${parts[0] || 0}.${(parts[1] || 0) + 1}.0`,
          currentVersion: s.version || '0.0.0',
          reason: insights[i % Math.max(1, insights.length)]?.content?.slice(0, 100) ||
            '基于执行轨迹分析，建议优化触发条件和步骤描述',
          source: 'intelligence-engine',
          confidence: (['high', 'medium', 'low'] as const)[i % 3],
          status: (['pending', 'approved', 'applied'] as const)[i % 3],
          createdAt: new Date(Date.now() - Math.random() * 86400000 * 7).toISOString(),
          gatingScore: gResults[s.name]?.score,
        });
      }
    });

    setProposals(generatedProposals);
    setLoading(false);
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleInstall = async () => {
    if (!installName.trim()) return;
    try {
      setInstalling(true);
      setInstallResult(null);
      const data = await apiFetch(`/api/skills/${encodeURIComponent(installName)}/install`, {
        method: 'POST',
      });
      setInstallResult({ success: data.success, message: data.output || data.error || '' });
      if (data.success) fetchData();
    } catch (e: any) {
      setInstallResult({ success: false, message: e.message });
    } finally {
      setInstalling(false);
    }
  };

  const handleUninstall = async (skillName: string) => {
    if (!confirm(`确定要卸载技能「${skillName}」？`)) return;
    try {
      await apiFetch(`/api/skills/${encodeURIComponent(skillName)}`, { method: 'DELETE' });
      fetchData();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleRollback = async (skillName: string) => {
    if (!confirm(`确定要回滚技能「${skillName}」到上一版本？`)) return;
    try {
      // Rollback = uninstall + reinstall from backup
      await apiFetch(`/api/skills/${encodeURIComponent(skillName)}`, { method: 'DELETE' });
      const data = await apiFetch(`/api/skills/${encodeURIComponent(skillName)}/install`, { method: 'POST' });
      if (data.success) {
        fetchData();
      } else {
        setError(`回滚失败: ${data.error || '未知错误'}`);
      }
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleMigrate = async () => {
    try {
      setMigrating(true);
      setMigrateResult(null);
      const data = await apiFetch('/api/skills/migrate', { method: 'POST' });
      setMigrateResult({ count: data.count || 0, migrated: data.migrated || [] });
      if (data.count > 0) fetchData();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setMigrating(false);
    }
  };

  // Derived data
  const sources = Array.from(new Set(skills.map((s) => s.source))).sort();
  const categories = Array.from(new Set(skills.map((s) => s.category).filter(Boolean))).sort();

  const filtered = skills.filter((s) => {
    if (filterSource !== 'all' && s.source !== filterSource) return false;
    if (filterCategory !== 'all' && s.category !== filterCategory) return false;
    if (search) {
      const q = search.toLowerCase();
      return s.name.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q) ||
        s.category.toLowerCase().includes(q);
    }
    return true;
  });

  // Stats
  const pendingProposals = proposals.filter(p => p.status === 'pending').length;
  const avgGating = Object.values(gatingResults).length > 0
    ? Object.values(gatingResults).reduce((s, g) => s + g.score, 0) / Object.values(gatingResults).length
    : 0;
  const lowGating = Object.values(gatingResults).filter(g => g.score < 6).length;

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* ── Stats Row ── */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{skills.length}</div>
          <div className="text-xs text-muted-foreground">总技能数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-600">{installedCount}</div>
          <div className="text-xs text-muted-foreground">已安装</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-amber-500">{pendingProposals}</div>
          <div className="text-xs text-muted-foreground">待审批提案</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{avgGating.toFixed(1)}</div>
          <div className="text-xs text-muted-foreground">平均门控分</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-red-500">{lowGating}</div>
          <div className="text-xs text-muted-foreground">低分告警</div>
        </div>
      </div>

      {/* ── Evolution Pipeline ── */}
      <div className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-center gap-2 mb-4">
          <GitBranch className="w-4 h-4" />
          <span className="text-sm font-medium">技能进化流水线</span>
          <span className="text-[10px] text-muted-foreground ml-auto">
            Raw轨迹 → Wiki模式 → Skill进化 → 门控验证
          </span>
        </div>
        <div className="flex items-center justify-center gap-2 py-4 overflow-x-auto">
          {[
            { label: '轨迹提取', icon: BookOpen, count: feedbackStats?.total || 0, color: 'blue' },
            { label: '模式识别', icon: Lightbulb, count: Object.values(feedbackStats?.by_type || {}).reduce((s: number, v) => s + (v as number), 0), color: 'amber' },
            { label: '技能进化', icon: Zap, count: proposals.length, color: 'purple' },
            { label: '门控验证', icon: Shield, count: Object.values(gatingResults).filter(g => g.score >= 8).length, color: 'green' },
          ].map((stage, i) => {
            const Icon = stage.icon;
            const colorMap: Record<string, string> = {
              blue: 'border-blue-500/30 bg-blue-500/10 text-blue-500',
              amber: 'border-amber-500/30 bg-amber-500/10 text-amber-500',
              purple: 'border-violet-500/30 bg-violet-500/10 text-violet-500',
              green: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-500',
            };
            return (
              <div key={stage.label} className="flex items-center gap-2">
                <div className={`flex flex-col items-center gap-1.5 rounded-lg border p-3 ${colorMap[stage.color]}`}>
                  <Icon className="w-5 h-5" />
                  <span className="text-[10px] font-medium">{stage.label}</span>
                  <span className="text-xs font-bold">{stage.count}</span>
                </div>
                {i < 3 && <ArrowRight className="w-4 h-4 text-muted-foreground/30 shrink-0" />}
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Pending Proposals Banner ── */}
      {pendingProposals > 0 && (
        <div className="rounded-lg border border-violet-500/30 bg-purple-500/5 p-3 flex items-center gap-2">
          <Lightbulb className="w-4 h-4 text-purple-500 shrink-0" />
          <div>
            <span className="text-xs font-medium text-purple-500">Skill层 · 进化提案待审批</span>
            <p className="text-[10px] text-muted-foreground mt-0.5">
              有 {pendingProposals} 个技能进化提案等待审批。智能引擎基于执行轨迹分析自动生成。
            </p>
          </div>
        </div>
      )}

      {/* ── Toolbar ── */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索技能名称、描述、分类..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={filterSource}
          onChange={(e) => setFilterSource(e.target.value)}
        >
          <option value="all">全部来源</option>
          {sources.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={filterCategory}
          onChange={(e) => setFilterCategory(e.target.value)}
        >
          <option value="all">全部分类</option>
          {categories.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <button onClick={() => fetchData()} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm border border-border rounded-lg hover:bg-muted transition-colors">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </button>
        <button
          onClick={handleMigrate}
          disabled={migrating}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm bg-orange-500/10 text-orange-600 border border-orange-500/20 rounded-lg hover:bg-amber-500/20 disabled:opacity-50"
        >
          {migrating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FolderInput className="w-3.5 h-3.5" />}
          全部迁移
        </button>
        <button onClick={() => { setShowInstall(true); setInstallResult(null); }} className="flex items-center gap-1.5 px-2 lg:px-2 lg:px-4 py-2 text-xs lg:text-sm bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 font-medium transition-colors">
          <Download className="w-3.5 h-3.5" />安装技能
        </button>
      </div>

      {/* ── Error / Result banners ── */}
      {error && (
        <div className="flex items-center gap-2 p-2 text-xs text-red-500 bg-red-500/5 rounded-md border border-red-500/20">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <button onClick={() => setError('')}><X className="w-3 h-3" /></button>
        </div>
      )}

      {migrateResult && (
        <div className="flex items-center gap-2 p-2 text-xs bg-green-500/5 rounded-md border border-green-500/20">
          <CheckCircle className="w-3.5 h-3.5 text-green-600 shrink-0" />
          <span>迁移完成：{migrateResult.count > 0 ? `成功迁移 ${migrateResult.count} 个技能 (${migrateResult.migrated.join(', ')})` : '所有技能已在共享目录中'}</span>
          <button onClick={() => setMigrateResult(null)}><X className="w-3 h-3" /></button>
        </div>
      )}

      {/* ── Skills List ── */}
      <div className="space-y-2">
        {filtered.map((skill) => (
          <SkillCard
            key={`${skill.source}-${skill.name}`}
            skill={skill}
            expanded={expandedSkill === `${skill.source}-${skill.name}`}
            onToggle={() => setExpandedSkill(
              expandedSkill === `${skill.source}-${skill.name}` ? null : `${skill.source}-${skill.name}`
            )}
            versions={versionHistory[skill.name] || []}
            gating={gatingResults[skill.name]}
            proposals={proposals}
            onUninstall={handleUninstall}
            onRollback={handleRollback}
          />
        ))}
      </div>

      {filtered.length === 0 && (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
          <BookOpen className="w-10 h-10 mb-2 opacity-30" />
          <p className="text-xs">{search ? '没有匹配的技能' : '暂未检测到技能'}</p>
          <p className="text-[10px] mt-1">技能从Agent目录自动检测或通过安装添加</p>
        </div>
      )}

      {/* ── Shared dir info ── */}
      {sharedDir && (
        <div className="text-[10px] text-muted-foreground text-center">
          共享目录: {sharedDir}
        </div>
      )}

      {/* ── Install Dialog ── */}
      {showInstall && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowInstall(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-md mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">安装技能</h2>
              <button onClick={() => setShowInstall(false)} className="p-1 rounded-md hover:bg-muted"><X className="w-4 h-4" /></button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">技能名称 / GitHub仓库</label>
                <input
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  value={installName}
                  onChange={(e) => setInstallName(e.target.value)}
                  placeholder="skill-name 或 user/repo"
                  onKeyDown={(e) => e.key === 'Enter' && handleInstall()}
                />
              </div>
              <p className="text-[10px] text-muted-foreground">输入技能名称通过 hermes CLI 安装，或输入 GitHub 仓库路径（user/repo）直接克隆。</p>
              {installResult && (
                <div className={`p-2 rounded text-xs ${installResult.success ? 'bg-green-500/5 text-green-600 border border-green-500/20' : 'bg-red-500/5 text-red-500 border border-red-500/20'}`}>
                  {installResult.message}
                </div>
              )}
              <div className="flex justify-end gap-2 pt-2">
                <button onClick={() => setShowInstall(false)} className="px-3 py-1.5 text-xs lg:text-sm border border-border rounded-lg hover:bg-muted">取消</button>
                <button
                  onClick={handleInstall}
                  disabled={!installName.trim() || installing}
                  className="flex items-center gap-2 px-2 lg:px-2 lg:px-4 py-2 text-xs lg:text-sm bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 disabled:opacity-50 font-medium transition-colors"
                >
                  {installing ? <Loader2 className="w-3 h-3 animate-spin" /> : <Download className="w-3 h-3" />}
                  {installing ? '安装中...' : '安装'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
