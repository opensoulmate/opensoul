import * as React from 'react';
import { cn } from '@opensoulmate/openface';

// 共享组件层——按钮解剖单一源（防 78 页手抄类串漂移）。
// 正典来源：OpenMate hippo 平行页实测解剖（C3-④ 逐像素对齐，见 docs/dev-roadmap.md 2026-10-11 06:15）：
//   primary   = rounded-lg px-2 lg:px-4 py-2 text-xs lg:text-sm font-medium hover:bg-primary/90 gap-2
//   secondary = 描边款 rounded-lg border border-border px-3 py-1.5 text-xs lg:text-sm hover:bg-muted transition-colors
//   图标按钮  = rounded-md p-1.5
// variant/size 命名对齐 ~/openmate/src/components/ui/button.tsx（同名便于跨仓对照；值以实测正典为准）。
// 改按钮观感只改这里。

const BUTTON_BASE =
  'inline-flex items-center justify-center rounded-lg text-xs lg:text-sm whitespace-nowrap transition-colors disabled:opacity-50 disabled:pointer-events-none';

const BUTTON_VARIANTS: Record<string, string> = {
  primary: 'bg-primary text-primary-foreground hover:bg-primary/90 px-2 lg:px-4 py-2 font-medium gap-2',
  secondary: 'border border-border hover:bg-muted px-3 py-1.5 gap-1.5',
  ghost: 'text-muted-foreground hover:text-foreground hover:bg-muted/50 px-2 lg:px-4 py-2 gap-2',
  destructive: 'bg-destructive text-white hover:bg-destructive/90 px-2 lg:px-4 py-2 font-medium gap-2',
  link: 'text-primary underline-offset-4 hover:underline gap-1',
};

// 尺寸为纯覆盖（twMerge 后写胜出）：默认=变体自带解剖
const BUTTON_SIZES: Record<string, string> = {
  default: '',
  sm: 'px-3 py-1.5 gap-1.5',
  xs: 'px-2 py-1 text-[10px] gap-1',
  icon: 'rounded-md p-1.5',
};

type ButtonVariant = keyof typeof BUTTON_VARIANTS;
type ButtonSize = keyof typeof BUTTON_SIZES;

export function buttonVariants({
  variant = 'primary',
  size = 'default',
  className,
}: { variant?: ButtonVariant; size?: ButtonSize; className?: string } = {}) {
  return cn(BUTTON_BASE, BUTTON_VARIANTS[variant], BUTTON_SIZES[size], className);
}

export function Button({
  variant = 'primary',
  size = 'default',
  className,
  ...props
}: React.ComponentProps<'button'> & { variant?: ButtonVariant; size?: ButtonSize }) {
  return (
    <button
      data-slot="button"
      data-variant={variant}
      data-size={size}
      className={buttonVariants({ variant, size, className })}
      {...props}
    />
  );
}
