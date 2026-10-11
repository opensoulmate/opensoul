import * as React from 'react';
import { cn } from '@opensoulmate/openface';

// 共享组件层——按钮解剖单一源（防 78 页手抄类串漂移）。
// 正典来源：OpenMate hippo 平行页实测解剖（C3-④ 逐像素对齐，见 docs/dev-roadmap.md 2026-10-11 06:15）：
//   primary   = rounded-lg px-2 lg:px-4 py-2 text-xs lg:text-sm font-medium hover:bg-primary/90 gap-2
//   secondary = 描边款 rounded-lg border border-border px-3 py-1.5 text-xs lg:text-sm hover:bg-muted transition-colors
//   图标按钮  = rounded-md p-1.5
// variant/size 命名对齐 ~/openmate/src/components/ui/button.tsx（同名便于跨仓对照；值以实测正典为准）。
// 改按钮观感只改这里。
//
// 解剖分两层（C3-⑧，2026-10-11）：
//   TIER1 正典工具栏按钮（primary/secondary/ghost/destructive/link + sizes）= BUTTON_BASE+变体，语义见上。
//   TIER2 族收口（icon-*/chip-*/row/tabline/bare）= 族形状原样搬入（间距/圆角/字阶——漂移主源），
//     不注入 BUTTON_BASE 防污染裸解剖；色/位移/禁用语义一律留调用点（语义色非 chrome 令牌，同图表色板纪律）。
//   bare = 零解剖注入收口（长尾/动态模板/expr），className 全量透传——迁移后 computed style 集合与原样等价。

const BUTTON_BASE =
  'inline-flex items-center justify-center rounded-lg text-xs lg:text-sm whitespace-nowrap transition-colors disabled:opacity-50 disabled:pointer-events-none';

const TIER1_VARIANTS: Record<string, string> = {
  primary: 'bg-primary text-primary-foreground hover:bg-primary/90 px-2 lg:px-4 py-2 font-medium gap-2',
  secondary: 'border border-border hover:bg-muted px-3 py-1.5 gap-1.5',
  ghost: 'text-muted-foreground hover:text-foreground hover:bg-muted/50 px-2 lg:px-4 py-2 gap-2',
  destructive: 'bg-destructive text-white hover:bg-destructive/90 px-2 lg:px-4 py-2 font-medium gap-2',
  link: 'text-primary underline-offset-4 hover:underline gap-1',
};

// TIER2 族形状（C3-⑧ census 382 静态 + 72 模板串实测正典逐字）：
const SHAPE_VARIANTS: Record<string, string> = {
  bare: '',
  'icon-xs': 'rounded-md p-0.5',
  'icon-sm': 'rounded-md p-1',
  'icon-md': 'rounded-md p-1.5',
  'chip-xs': 'px-2 py-1 rounded-lg text-[10px]',
  'chip-sm': 'px-3 py-1.5 rounded-lg text-xs lg:text-sm',
  'chip-lg': 'px-4 py-2 rounded-lg text-xs lg:text-sm',
  row: 'flex items-center w-full text-left',
  tabline: 'flex items-center gap-1.5 px-3 py-2 text-xs lg:text-sm border-b-2 transition-colors',
};

// 尺寸为纯覆盖（twMerge 后写胜出）：默认=变体自带解剖
const BUTTON_SIZES: Record<string, string> = {
  default: '',
  sm: 'px-3 py-1.5 gap-1.5',
  xs: 'px-2 py-1 text-[10px] gap-1',
  icon: 'rounded-md p-1.5',
};

type ButtonVariant = keyof typeof TIER1_VARIANTS | keyof typeof SHAPE_VARIANTS;
type ButtonSize = keyof typeof BUTTON_SIZES;

export function buttonVariants({
  variant = 'primary',
  size = 'default',
  className,
}: { variant?: ButtonVariant; size?: ButtonSize; className?: string } = {}) {
  const shape = (SHAPE_VARIANTS as Record<string, string>)[variant];
  if (shape !== undefined) {
    // TIER2：bare 为 '' → 纯透传；族形状 + 残差（残差后写胜出，色/位移留调用点）
    return cn(shape, className);
  }
  return cn(BUTTON_BASE, (TIER1_VARIANTS as Record<string, string>)[variant], BUTTON_SIZES[size], className);
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
