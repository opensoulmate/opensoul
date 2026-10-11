import * as React from 'react';
import { cn } from '@opensoulmate/openface';

// 共享组件层——徽章/标签解剖单一源（防 78 页手抄类串漂移）。
// 正典来源：C3-④ pill 逐像素对齐（见 docs/dev-roadmap.md 2026-10-11 06:15）：
//   rounded-full px-2 py-0.5 text-[10px] font-medium，语义色 green→emerald / orange→amber / purple→violet。
// variant 命名对齐 ~/openmate/src/components/ui/badge.tsx（default/success/warning/destructive 同名）；
// 色值以本仓实测正典为准（OM ui/badge.tsx 是 text-[11px]·/15 旧配色，正典取 hippo 平行页实测的 text-[10px]·/10）。
// 动态色（config 映射表）走 className 透传，twMerge 后写胜出。改徽章观感只改这里。
// bare = 零色注入（C3-⑨，2026-10-11）：只注解剖（BADGE_BASE），色全量留调用点——
//   动态模板 pill（${statusColor(...)} 等 helper 色表驱动）专用，与 button.tsx TIER2 bare 同纪律。

const BADGE_BASE = 'inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium';

const BADGE_VARIANTS: Record<string, string> = {
  bare: '',
  default: 'bg-primary/10 text-primary',
  subtle: 'bg-primary/5 text-primary/70',
  muted: 'bg-muted text-muted-foreground',
  success: 'bg-emerald-500/10 text-emerald-500',
  warning: 'bg-amber-500/10 text-amber-500',
  destructive: 'bg-red-500/10 text-red-500',
  info: 'bg-blue-500/10 text-blue-500',
  accent: 'bg-violet-500/10 text-violet-500',
};

export type BadgeVariant = keyof typeof BADGE_VARIANTS;

export function Badge({
  variant = 'default',
  className,
  ...props
}: React.ComponentProps<'span'> & { variant?: BadgeVariant }) {
  return (
    <span
      data-slot="badge"
      data-variant={variant}
      className={cn(BADGE_BASE, BADGE_VARIANTS[variant], className)}
      {...props}
    />
  );
}
