import * as React from 'react';
import { cn } from '@opensoulmate/openface';

// 共享组件层——内联码片（<code>）解剖单一源（C3-⑪，2026-10-11）。
// 正典来源：census 10 处 <code> 族形（主导 chip 族 5/10 = 'text-xs bg-muted px-1.5 py-0.5 rounded'）；
//   对照 OM 正典同族（quick-notes 'bg-muted px-1 py-0.5 rounded text-xs font-mono'、
//   marrow 'px-2 py-0.5 rounded bg-muted text-xs font-mono'）：bg-muted + rounded + text-xs 同解剖。
// 解剖纪律同 button.tsx TIER2：族形状原样搬入，不注入 BASE 防污染裸解剖（bare 零注入）；
//   font-mono/色/字阶覆盖一律留调用点（twMerge 后写胜出，迁移 token 集合等价=零视觉变化）。
// font-mono 不进族形的实证：构建 CSS --default-mono-font-family:var(--font-mono)，
//   code 元素 preflight 即取同一字栈——留调用点与族形注入渲染等价，取前者保集合等价。
// 改码片观感只改这里。

// 族形状（C3-⑪ census 10 处 <code> 实测正典逐字）：
const CODE_VARIANTS: Record<string, string> = {
  bare: '',
  chip: 'rounded bg-muted px-1.5 py-0.5 text-xs',
  'chip-sm': 'rounded bg-muted px-1',
  'chip-lg': 'rounded bg-muted px-2 py-1 text-xs',
};

export type CodeVariant = keyof typeof CODE_VARIANTS;

export function codeVariants({
  variant = 'chip',
  className,
}: { variant?: CodeVariant; className?: string } = {}) {
  return cn(CODE_VARIANTS[variant], className);
}

export function Code({
  variant = 'chip',
  className,
  ...props
}: React.ComponentProps<'code'> & { variant?: CodeVariant }) {
  return (
    <code
      data-slot="code"
      data-variant={variant}
      className={codeVariants({ variant, className })}
      {...props}
    />
  );
}
