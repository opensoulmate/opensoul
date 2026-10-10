// ── 主题系统（C3-⑤ light/purple 主题对齐）──────────────────────────────
// 语义正典对齐 ~/openmate/src/lib/theme.ts（applyTheme 类名语义逐字一致）：
//   dark   → html.dark                （:root 令牌 = DS v2.0 Dracula）
//   light  → html.light               （.light 块覆盖令牌）
//   purple → html.dark.theme-purple   （.theme-purple 块覆盖令牌）
// 颜色值单一源只在 app/globals.css（本文件零色值，换主题只改 CSS）。
// 不用 openface ThemeProvider：其 token 集是 8 月旧配色（#09090b/#7c3aed），
// applyTheme 会以 inline style 覆盖变量，冲掉 DS v2.0 对齐值。

export type ThemeId = 'dark' | 'light' | 'purple';

/** localStorage 存储键名 */
const STORAGE_KEY = 'opensoul-theme';

/** 已知主题类（切换前全清防残留，与 OpenMate applyTheme 同纪律） */
const ALL_THEME_CLASSES = ['dark', 'light', 'theme-purple'];

/**
 * 读取存储主题；非法值回退 dark（SSR / 无 localStorage 同样回退）。
 */
export function getStoredTheme(): ThemeId {
  if (typeof window === 'undefined') return 'dark';
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === 'light' || stored === 'purple') return stored;
  return 'dark';
}

/**
 * 应用主题到 document.documentElement（类名语义与 OpenMate 逐字一致）。
 * 颜色全部来自 CSS 令牌，此处只切类名/属性。
 */
export function applyTheme(theme: ThemeId) {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  root.classList.remove(...ALL_THEME_CLASSES);
  root.setAttribute('data-theme', theme);
  if (theme === 'light') {
    root.classList.add('light');
  } else if (theme === 'purple') {
    root.classList.add('dark', 'theme-purple');
  } else {
    root.classList.add('dark');
  }
}

/** 持久化到 localStorage 并立即应用 */
export function persistTheme(theme: ThemeId) {
  if (typeof window === 'undefined') return;
  localStorage.setItem(STORAGE_KEY, theme);
  applyTheme(theme);
}
