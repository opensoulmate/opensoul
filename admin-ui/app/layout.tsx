import type { Metadata } from "next";
import "./globals.css";
import { AppWrapper } from "./app-wrapper";

export const metadata: Metadata = {
  title: "OpenSoul 管理后台",
  description: "OpenSoul Administration Panel",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className="dark" suppressHydrationWarning>
      <body className="antialiased">
        {/* 主题防闪烁：首帧前按 localStorage 设好 html 类名（SSR 默认 dark）。
            类名语义与 lib/theme.ts applyTheme 一致。 */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "(function(){try{var t=localStorage.getItem('opensoul-theme');if(t!=='light'&&t!=='purple')return;var r=document.documentElement;r.classList.remove('dark');if(t==='light'){r.classList.add('light');}else{r.classList.add('dark','theme-purple');}r.setAttribute('data-theme',t);}catch(e){}})();",
          }}
        />
        <AppWrapper>{children}</AppWrapper>
      </body>
    </html>
  );
}
