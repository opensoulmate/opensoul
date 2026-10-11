'use client';

// ── 主题切换（C3-⑤）：dark/light/purple 三态，图标与 OpenMate 设置页
// 主题 ButtonGroup 同语义（Moon/Sun/Palette）。点击即持久化+应用。 ──

import { useEffect, useState } from 'react';
import { Moon, Sun, Palette } from 'lucide-react';
import { cn } from '@opensoulmate/openface';
import { type ThemeId, getStoredTheme, persistTheme, applyTheme } from '@/lib/theme';
import { Button } from '../components/ui/button';

const OPTIONS: { id: ThemeId; label: string; Icon: typeof Moon }[] = [
  { id: 'dark', label: '深色', Icon: Moon },
  { id: 'light', label: '浅色', Icon: Sun },
  { id: 'purple', label: '紫色', Icon: Palette },
];

export function ThemeSwitcher() {
  const [theme, setTheme] = useState<ThemeId | null>(null);

  // 挂载后接管内联防闪烁脚本：同步一次应用态（水合后以客户端为准）+ 回读当前主题
  useEffect(() => {
    const stored = getStoredTheme();
    applyTheme(stored);
    setTheme(stored);
  }, []);

  return (
    <div className="flex items-center gap-0.5" data-testid="theme-switcher">
      {OPTIONS.map(({ id, label, Icon }) => (
        <Button variant="bare"
          key={id}
          type="button"
          title={`主题：${label}`}
          aria-label={`主题：${label}`}
          aria-pressed={theme === id}
          onClick={() => {
            persistTheme(id);
            setTheme(id);
          }}
          className={cn(
            'rounded-md p-1.5 transition-colors',
            theme === id
              ? 'bg-primary/10 text-primary'
              : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
          )}
        >
          <Icon className="w-3.5 h-3.5" />
        </Button>
      ))}
    </div>
  );
}
