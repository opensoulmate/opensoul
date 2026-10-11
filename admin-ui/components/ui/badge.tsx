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
// TONES = 动态色色表单一源（C3-⑩ 色表归一，2026-10-11）：helper 色表（statusColor/config/severity
//   等映射表）一律引用 TONES 槽位，禁再手抄色串——正典 pill 色对=bg-X-500/10 + text-X-500（X=8 语义族），
//   非正典色（green/orange/purple/yellow/cyan/pink/teal/indigo/sky 族、-400/-600/-700 色阶）不得进 pill。
//   同表多条目撞同一槽位时允许共 tone（图标/文字区分），正典无层级色阶。
//   fill=实心通道（状态点等语义实心），border=描边通道（/30，pill 描边）。
//   数据可视化色板（图表阈值条、图谱节点 hex、内联 style hex、统计数字）不属色表，按 C3-④ 裁决留调用点。

export type ToneName = 'primary' | 'subtle' | 'muted' | 'success' | 'warning' | 'destructive' | 'info' | 'accent';

export const TONES: Record<ToneName, { bg: string; text: string; border: string; fill: string }> = {
  primary: { bg: 'bg-primary/10', text: 'text-primary', border: 'border-primary/20', fill: 'bg-primary' },
  subtle: { bg: 'bg-primary/5', text: 'text-primary/70', border: 'border-primary/20', fill: 'bg-primary/70' },
  muted: { bg: 'bg-muted', text: 'text-muted-foreground', border: 'border-border', fill: 'bg-muted-foreground/30' },
  success: { bg: 'bg-emerald-500/10', text: 'text-emerald-500', border: 'border-emerald-500/30', fill: 'bg-emerald-500' },
  warning: { bg: 'bg-amber-500/10', text: 'text-amber-500', border: 'border-amber-500/30', fill: 'bg-amber-500' },
  destructive: { bg: 'bg-red-500/10', text: 'text-red-500', border: 'border-red-500/30', fill: 'bg-red-500' },
  info: { bg: 'bg-blue-500/10', text: 'text-blue-500', border: 'border-blue-500/30', fill: 'bg-blue-500' },
  accent: { bg: 'bg-violet-500/10', text: 'text-violet-500', border: 'border-violet-500/30', fill: 'bg-violet-500' },
};

// 色对复合助手：`${bg} ${text}`（bare Badge className 或 helper 色表 color 字段用）
export function tone(name: ToneName): string {
  return `${TONES[name].bg} ${TONES[name].text}`;
}

// 色对+描边复合助手（pill 带 border 时用）
export function toneBordered(name: ToneName): string {
  return `${TONES[name].bg} ${TONES[name].text} ${TONES[name].border}`;
}

const BADGE_BASE = 'inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium';

const BADGE_VARIANTS: Record<string, string> = {
  bare: '',
  default: tone('primary'),
  subtle: tone('subtle'),
  muted: tone('muted'),
  success: tone('success'),
  warning: tone('warning'),
  destructive: tone('destructive'),
  info: tone('info'),
  accent: tone('accent'),
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
