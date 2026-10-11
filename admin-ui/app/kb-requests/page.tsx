'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, RefreshCw, X, CheckCircle, XCircle, Clock,
  FileText, Share2, Filter, MessageSquare, User, AlertCircle,
  ChevronDown, ChevronRight, Plus, Loader2, BookOpen, Shield,
} from 'lucide-react';
import { Tab, TabBar } from '@/components/ui/tab';
import { Button } from '@/components/ui/button';
import { Badge, TONES } from '@/components/ui/badge';

// ── Types ───────────────────────────────────────────────────

interface KnowledgeRequest {
  id: string;
  user_id: string;
  user_name: string;
  kb_name: string;
  kb_description: string;
  status: string; // pending | approved | rejected
  reviewer_id?: string;
  review_note?: string;
  reviewed_at?: string;
  created_at: string;
}

interface SharingRequest {
  id: string;
  user_id: string;
  user_name: string;
  kb_id: string;
  kb_name: string;
  status: string;
  reviewer_id?: string;
  review_note?: string;
  reviewed_at?: string;
  created_at: string;
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

function StatusBadge({ status }: { status: string }) {
  const config: Record<string, { bg: string; text: string; icon: typeof Clock; label: string }> = {
    pending: { bg: TONES.warning.bg, text: TONES.warning.text, icon: Clock, label: '待审批' },
    approved: { bg: TONES.success.bg, text: TONES.success.text, icon: CheckCircle, label: '已通过' },
    rejected: { bg: TONES.destructive.bg, text: TONES.destructive.text, icon: XCircle, label: '已拒绝' },
  };
  const c = config[status] || config.pending;
  const Icon = c.icon;
  return (
    <Badge variant="bare" className={`gap-1 ${c.bg} ${c.text}`}>
      <Icon className="w-2.5 h-2.5" />{c.label}
    </Badge>
  );
}

// ── Request Card ────────────────────────────────────────────

function RequestCard({
  request, type, expanded, onToggle, onReview, reviewing,
}: {
  request: KnowledgeRequest | SharingRequest;
  type: 'create' | 'sharing';
  expanded: boolean;
  onToggle: () => void;
  onReview: (id: string, status: string, note: string) => void;
  reviewing: boolean;
}) {
  const [reviewNote, setReviewNote] = useState('');
  const isPending = request.status === 'pending';

  return (
    <div className={`border rounded-lg bg-card transition-colors ${
      request.status === 'approved' ? 'border-green-500/20' :
      request.status === 'rejected' ? 'border-red-500/20' :
      'border-border'
    }`}>
      <Button
        onClick={onToggle}
        variant="row" className="gap-3 p-3 hover:bg-muted/30 transition-colors"
      >
        <div className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${
          type === 'create' ? 'bg-blue-500/10' : 'bg-violet-500/10'
        }`}>
          {type === 'create' ? (
            <FileText className="w-5 h-5 text-blue-500" />
          ) : (
            <Share2 className="w-5 h-5 text-violet-500" />
          )}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium truncate">
              {type === 'create' ? (request as KnowledgeRequest).kb_name : (request as SharingRequest).kb_name}
            </span>
            <StatusBadge status={request.status} />
            <Badge variant="muted">
              {type === 'create' ? '创建申请' : '共享申请'}
            </Badge>
          </div>
          <div className="flex items-center gap-3 mt-0.5 text-[10px] text-muted-foreground">
            <span className="flex items-center gap-1">
              <User className="w-2.5 h-2.5" />{request.user_name}
            </span>
            <span className="flex items-center gap-1">
              <Clock className="w-2.5 h-2.5" />{formatRelative(request.created_at)}
            </span>
            {request.reviewer_id && (
              <span className="flex items-center gap-1">
                <Shield className="w-2.5 h-2.5" />已审核
              </span>
            )}
          </div>
        </div>

        <div className="text-right shrink-0 mr-2">
          <div className="text-xs text-muted-foreground">{formatTime(request.created_at)}</div>
          {request.reviewed_at && (
            <div className="text-[10px] text-muted-foreground">审核: {formatTime(request.reviewed_at)}</div>
          )}
        </div>

        {expanded ? <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 text-muted-foreground shrink-0" />}
      </Button>

      {expanded && (
        <div className="px-3 pb-3 border-t border-border pt-2 space-y-3">
          {/* Details */}
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div>
              <span className="text-muted-foreground">申请人:</span>{' '}
              <span className="font-medium">{request.user_name}</span>
            </div>
            <div>
              <span className="text-muted-foreground">申请时间:</span>{' '}
              <span>{formatTime(request.created_at)}</span>
            </div>
            {type === 'create' && (
              <>
                <div className="col-span-2">
                  <span className="text-muted-foreground">知识库名称:</span>{' '}
                  <span className="font-medium">{(request as KnowledgeRequest).kb_name}</span>
                </div>
                {(request as KnowledgeRequest).kb_description && (
                  <div className="col-span-2">
                    <span className="text-muted-foreground">描述:</span>{' '}
                    <span>{(request as KnowledgeRequest).kb_description}</span>
                  </div>
                )}
              </>
            )}
            {type === 'sharing' && (
              <>
                <div>
                  <span className="text-muted-foreground">知识库ID:</span>{' '}
                  <span className="font-mono text-[10px]">{(request as SharingRequest).kb_id}</span>
                </div>
                <div>
                  <span className="text-muted-foreground">知识库名称:</span>{' '}
                  <span className="font-medium">{(request as SharingRequest).kb_name}</span>
                </div>
              </>
            )}
          </div>

          {/* Review result */}
          {request.review_note && (
            <div className={`rounded-lg border p-3 ${
              request.status === 'approved' ? 'border-green-500/20 bg-green-500/5' : 'border-red-500/20 bg-red-500/5'
            }`}>
              <div className="flex items-center gap-1.5 mb-1">
                <MessageSquare className="w-3 h-3 text-muted-foreground" />
                <span className="text-xs font-medium text-muted-foreground">审核意见</span>
              </div>
              <p className="text-xs">{request.review_note}</p>
            </div>
          )}

          {/* Review actions (only for pending) */}
          {isPending && (
            <div className="space-y-2">
              <textarea
                className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md resize-none h-16 focus:outline-none focus:ring-1 focus:ring-primary/50"
                placeholder="审核意见（可选）..."
                value={reviewNote}
                onChange={(e) => setReviewNote(e.target.value)}
              />
              <div className="flex items-center gap-2">
                <Button
                  onClick={() => onReview(request.id, 'approved', reviewNote)}
                  disabled={reviewing}
                  variant="chip-sm" className="flex items-center gap-1 bg-emerald-500/10 text-emerald-500 border border-emerald-500/20 hover:bg-emerald-500/20 disabled:opacity-50"
                >
                  {reviewing ? <Loader2 className="w-3 h-3 animate-spin" /> : <CheckCircle className="w-3 h-3" />}
                  通过
                </Button>
                <Button
                  onClick={() => onReview(request.id, 'rejected', reviewNote)}
                  disabled={reviewing}
                  variant="chip-sm" className="flex items-center gap-1 bg-red-500/10 text-red-500 border border-red-500/20 hover:bg-red-500/20 disabled:opacity-50"
                >
                  {reviewing ? <Loader2 className="w-3 h-3 animate-spin" /> : <XCircle className="w-3 h-3" />}
                  拒绝
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────

export default function KbRequestsPage() {
  const [tab, setTab] = useState<'create' | 'sharing'>('create');
  const [createRequests, setCreateRequests] = useState<KnowledgeRequest[]>([]);
  const [sharingRequests, setSharingRequests] = useState<SharingRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [reviewingId, setReviewingId] = useState<string | null>(null);

  const fetchRequests = useCallback(async () => {
    setLoading(true);
    setError('');
    const results = await Promise.allSettled([
      apiFetch('/api/knowledge-requests/'),
      apiFetch('/api/kb-sharing/'),
    ]);
    if (results[0].status === 'fulfilled') {
      setCreateRequests(Array.isArray(results[0].value) ? results[0].value : []);
    }
    if (results[1].status === 'fulfilled') {
      setSharingRequests(Array.isArray(results[1].value) ? results[1].value : []);
    }
    if (results[0].status === 'rejected' && results[1].status === 'rejected') {
      setError('获取数据失败');
    }
    setLoading(false);
  }, []);

  useEffect(() => { fetchRequests(); }, [fetchRequests]);

  const handleReview = useCallback(async (requestId: string, status: string, note: string) => {
    try {
      setReviewingId(requestId);
      // Try both endpoints
      let success = false;
      try {
        await apiFetch(`/api/knowledge-requests/${requestId}/review`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status, review_note: note }),
        });
        success = true;
      } catch {
        // Try sharing endpoint
        await apiFetch(`/api/kb-sharing/${requestId}/review`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status, review_note: note }),
        });
        success = true;
      }
      if (success) fetchRequests();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setReviewingId(null);
    }
  }, [fetchRequests]);

  // Stats
  const allCreate = createRequests;
  const allSharing = sharingRequests;
  const pendingCreate = allCreate.filter(r => r.status === 'pending').length;
  const pendingSharing = allSharing.filter(r => r.status === 'pending').length;
  const approvedCreate = allCreate.filter(r => r.status === 'approved').length;
  const approvedSharing = allSharing.filter(r => r.status === 'approved').length;
  const rejectedCreate = allCreate.filter(r => r.status === 'rejected').length;
  const rejectedSharing = allSharing.filter(r => r.status === 'rejected').length;

  // Current tab data
  const currentData = tab === 'create' ? allCreate : allSharing;

  // Filter
  const filtered = currentData.filter((r) => {
    if (statusFilter !== 'all' && r.status !== statusFilter) return false;
    if (search) {
      const q = search.toLowerCase();
      return r.user_name.toLowerCase().includes(q) ||
        r.kb_name.toLowerCase().includes(q) ||
        (r.id || '').toLowerCase().includes(q) ||
        ('kb_description' in r && (r as KnowledgeRequest).kb_description?.toLowerCase().includes(q));
    }
    return true;
  });

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{allCreate.length + allSharing.length}</div>
          <div className="text-xs text-muted-foreground">总申请数</div>
        </div>
        <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3">
          <div className="text-2xl font-bold text-amber-500">{pendingCreate + pendingSharing}</div>
          <div className="text-xs text-muted-foreground">待审批</div>
        </div>
        <div className="rounded-lg border border-green-500/20 bg-green-500/5 p-3">
          <div className="text-2xl font-bold text-green-600">{approvedCreate + approvedSharing}</div>
          <div className="text-xs text-muted-foreground">已通过</div>
        </div>
        <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-3">
          <div className="text-2xl font-bold text-red-500">{rejectedCreate + rejectedSharing}</div>
          <div className="text-xs text-muted-foreground">已拒绝</div>
        </div>
      </div>

      {/* Governance notice */}
      <div className="rounded-lg border border-blue-500/30 bg-blue-500/5 p-3 flex items-center gap-2">
        <Shield className="w-4 h-4 text-blue-500 shrink-0" />
        <div>
          <span className="text-xs font-medium text-blue-500">知识治理 · 审批工作流</span>
          <p className="text-[10px] text-muted-foreground mt-0.5">
            用户提交的知识库创建和共享申请在此审批。通过后自动创建知识库或标记为共享。
          </p>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索申请人、知识库名称..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="all">全部状态</option>
          <option value="pending">待审批</option>
          <option value="approved">已通过</option>
          <option value="rejected">已拒绝</option>
        </select>
        <TabBar>
          <Tab active={tab === 'create'} onClick={() => setTab('create')}>
            <FileText className="w-3 h-3" />创建 ({allCreate.length})
          </Tab>
          <Tab active={tab === 'sharing'} onClick={() => setTab('sharing')}>
            <Share2 className="w-3 h-3" />共享 ({allSharing.length})
          </Tab>
        </TabBar>
        <Button onClick={fetchRequests} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 p-2 text-xs text-red-500 bg-red-500/5 rounded-md border border-red-500/20">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <Button variant="bare" onClick={() => setError('')}><X className="w-3 h-3" /></Button>
        </div>
      )}

      {/* Request list */}
      <div className="space-y-2">
        {filtered.map((request) => (
          <RequestCard
            key={request.id}
            request={request}
            type={tab}
            expanded={expandedId === request.id}
            onToggle={() => setExpandedId(expandedId === request.id ? null : request.id)}
            onReview={handleReview}
            reviewing={reviewingId === request.id}
          />
        ))}
      </div>

      {filtered.length === 0 && (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
          {tab === 'create' ? (
            <FileText className="w-10 h-10 mb-2 opacity-30" />
          ) : (
            <Share2 className="w-10 h-10 mb-2 opacity-30" />
          )}
          <p className="text-xs">
            {search ? '没有匹配的申请' :
              tab === 'create' ? '暂无知识库创建申请' : '暂无知识库共享申请'}
          </p>
          <p className="text-[10px] mt-1">
            {tab === 'create'
              ? '用户可通过 API 提交知识库创建申请'
              : '用户可申请将个人知识库共享到企业知识库'}
          </p>
        </div>
      )}

      {/* Pending summary */}
      {(pendingCreate > 0 || pendingSharing > 0) && (
        <div className="text-center text-xs text-amber-500">
          {pendingCreate > 0 && <span>{pendingCreate} 个创建申请待审批</span>}
          {pendingCreate > 0 && pendingSharing > 0 && <span> · </span>}
          {pendingSharing > 0 && <span>{pendingSharing} 个共享申请待审批</span>}
        </div>
      )}
    </div>
  );
}
