import * as React from 'react';
import { cn } from '@opensoulmate/openface';

// 共享组件层——页内 tab 解剖单一源（防 78 页手抄类串漂移）。
// 正典来源：OM hippo 平行页 tab 实测（C3-④：36px/px-3 py-2/激活 bg-primary/10 text-primary font-medium 逐值全同），
// 形态取本仓 immune 页已对齐 tab 栏（分段容器 + 软激活）。改 tab 观感只改这里。

const TAB_BASE =
  'flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs lg:text-sm transition-colors whitespace-nowrap';

export function Tab({
  active = false,
  className,
  ...props
}: React.ComponentProps<'button'> & { active?: boolean }) {
  return (
    <button
      data-slot="tab"
      data-active={active || undefined}
      className={cn(
        TAB_BASE,
        active ? 'bg-primary/10 text-primary font-medium' : 'text-muted-foreground hover:text-foreground',
        className,
      )}
      {...props}
    />
  );
}

// 分段容器壳（slots）：解剖同 immune 页 tab 栏容器
export function TabBar({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      data-slot="tab-bar"
      className={cn('flex items-center gap-1 p-1 bg-muted rounded-lg w-fit overflow-x-auto', className)}
      {...props}
    />
  );
}
