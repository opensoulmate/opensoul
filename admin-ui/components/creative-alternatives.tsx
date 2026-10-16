'use client';

import { AlertCircle, Lightbulb } from 'lucide-react';
import { Badge } from '@/components/ui/badge';

// G1-③b-2 备选方案共享展示层——alternatives 归一载荷（count/items/(error)）结构化
// 渲染单一源。消费面：cortex 备选方案 tab（on-demand 生成/重生成）+ 任务规划结果
// （G1-③b-1 /api/cortex/plan 加性键 alternatives）。
// 诚实边界：①哨兵分类只在后端 alternatives_payload 单一源，本层零分类逻辑禁第二套
// 判法（error 键由后端归一给出）②字段缺失如实略不硬造（上游没发 pros/cons/评分就不
// 显示）③空载荷（无条目且无 error）=零注入无痕可撤。备选方案是软建议：不改任务决策。

export interface CreativeAlternativeItem {
  approach?: string;
  description?: string;
  pros?: any;
  cons?: any;
  novelty?: any;
  feasibility?: any;
  [key: string]: any;
}

export interface CreativeAlternativesPayload {
  count: number;
  items: CreativeAlternativeItem[];
  error?: string;
}

// 列表字段归一展示（数组→顿号连接、字符串→原样；其余/空=空串如实略）
function listLine(v: any): string {
  if (Array.isArray(v)) return v.map((x) => String(x)).join('、');
  if (typeof v === 'string' && v.trim()) return v.trim();
  return '';
}

// 评分字段（1-5 数字才显示，非数字如实略不硬造）
function scoreBadge(v: any, label: string) {
  return typeof v === 'number' ? <Badge variant="subtle">{label} {v}/5</Badge> : null;
}

export function CreativeAlternatives({ payload }: { payload?: CreativeAlternativesPayload }) {
  if (!payload) return null;
  const items: CreativeAlternativeItem[] = Array.isArray(payload.items) ? payload.items : [];
  const errorText = typeof payload.error === 'string' ? payload.error : '';
  if (!items.length && !errorText) return null;
  return (
    <div className="rounded-lg border border-border bg-muted/30 p-3 space-y-2">
      <div className="flex items-center gap-2">
        <Lightbulb className="w-3.5 h-3.5 text-violet-500" />
        <span className="text-xs font-medium">备选方案（创造力回流）</span>
        <Badge variant="muted">{items.length} 条</Badge>
        <span className="text-[10px] text-muted-foreground ml-auto">仅供参考，以目标要求为准，可全不采纳</span>
      </div>
      {errorText ? (
        <div className="flex items-start gap-2 p-2 text-xs text-amber-600 bg-amber-500/5 rounded-md border border-amber-500/20">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>创造力回流无产出：{errorText}</span>
        </div>
      ) : null}
      {items.map((it, i) => {
        const pros = listLine(it.pros);
        const cons = listLine(it.cons);
        return (
          <div key={i} className="flex items-start gap-3 p-2.5 rounded-lg border border-border bg-card">
            <div className="w-6 h-6 rounded-full bg-violet-500/10 text-violet-500 flex items-center justify-center text-[10px] font-bold shrink-0">
              {i + 1}
            </div>
            <div className="min-w-0 flex-1 space-y-1">
              <div className="flex items-center gap-2 flex-wrap">
                {it.approach ? <span className="text-xs font-medium">{it.approach}</span> : null}
                {scoreBadge(it.novelty, '新颖度')}
                {scoreBadge(it.feasibility, '可行性')}
              </div>
              {it.description ? <p className="text-xs text-muted-foreground">{it.description}</p> : null}
              {pros ? <div className="text-[10px] text-emerald-600">✓ {pros}</div> : null}
              {cons ? <div className="text-[10px] text-red-500/80">✗ {cons}</div> : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}
