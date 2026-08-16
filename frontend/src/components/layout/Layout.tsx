import { useEffect, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import {
  Activity, Radar, Wallet, Settings, Search, NotebookPen,
  Moon, Sun, ChevronsLeft, ChevronsRight, LineChart, Github,
  Database, Star, FileText, Swords, HeartPulse, Menu, X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useDarkMode } from "@/hooks/useDarkMode";
import { storageGet, storageSet } from "@/lib/storage";

// 具名导入：只把 version 打进产物，不会把整个 package.json 塞进 bundle
import { version as PKG_VERSION } from "../../../package.json";

// 版本号只从 package.json 读，不再各处写死（发 v0.3.0 时三处忘改停在 v0.2.2，#20）
const APP_VERSION = `v${PKG_VERSION}`;
const REPO_URL = "https://github.com/cxyfreedom/Vibe-Research";

const NAV = [
  { to: "/daily-review", icon: Activity, label: "每日复盘" },
  { to: "/intel", icon: Radar, label: "资讯雷达" },
  { to: "/market-center", icon: Database, label: "市场中心" },
  { to: "/data-health", icon: HeartPulse, label: "数据健康" },
  { to: "/stock-data", icon: Search, label: "个股数据" },
  { to: "/debate", icon: Swords, label: "多空辩论" },
  { to: "/watchlist", icon: Star, label: "自选股" },
  { to: "/portfolio", icon: Wallet, label: "我的持仓" },
  { to: "/my-reports", icon: FileText, label: "我的研报" },
  { to: "/notes", icon: NotebookPen, label: "研究记录" },
  { to: "/settings", icon: Settings, label: "接入 AI" },
];

export function Layout() {
  const { pathname } = useLocation();
  const { dark, toggle } = useDarkMode();
  const [collapsed, setCollapsed] = useState(() => storageGet("vr-sidebar") === "collapsed");
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    storageSet("vr-sidebar", collapsed ? "collapsed" : "expanded");
  }, [collapsed]);

  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  return (
    <div className="flex h-dvh min-w-0">
      {/* Mobile header */}
      <header className="fixed inset-x-0 top-0 z-[80] flex h-14 items-center justify-between border-b border-border/60 bg-background/90 px-3 backdrop-blur-xl md:hidden">
        <Link to="/daily-review" className="flex min-w-0 items-center gap-2">
          <LineChart className="h-5 w-5 shrink-0 text-primary text-glow" />
          <span className="truncate text-base font-extrabold tracking-tight">
            Vibe-<span className="text-primary">Research</span>
          </span>
        </Link>
        <div className="flex items-center gap-1">
          <button onClick={toggle} className="rounded-lg p-2 text-muted-foreground hover:bg-muted/50 hover:text-foreground" title={dark ? "亮色" : "暗色"}>
            {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
          <button onClick={() => setMobileOpen(true)} className="rounded-lg p-2 text-muted-foreground hover:bg-muted/50 hover:text-foreground" aria-label="打开导航">
            <Menu className="h-5 w-5" />
          </button>
        </div>
      </header>

      {mobileOpen && (
        <button className="fixed inset-0 z-[100] bg-black/60 backdrop-blur-sm md:hidden" onClick={() => setMobileOpen(false)} aria-label="关闭导航遮罩" />
      )}
      <aside className={cn(
        "glass fixed inset-y-2 left-2 z-[110] flex w-[min(18rem,calc(100vw-1rem))] flex-col rounded-2xl transition-transform duration-200 md:hidden",
        mobileOpen ? "translate-x-0" : "-translate-x-[calc(100%+1rem)]",
      )}>
        <div className="flex items-center justify-between border-b border-border/50 p-4">
          <Link to="/daily-review" className="flex items-center gap-2">
            <LineChart className="h-6 w-6 text-primary text-glow" />
            <span className="text-lg font-extrabold tracking-tight">Vibe-<span className="text-primary">Research</span></span>
          </Link>
          <button onClick={() => setMobileOpen(false)} className="rounded-lg p-2 text-muted-foreground hover:bg-muted/50 hover:text-foreground" aria-label="关闭导航">
            <X className="h-5 w-5" />
          </button>
        </div>
        <nav className="flex-1 space-y-1 overflow-y-auto p-2.5">
          {NAV.map(({ to, icon: Icon, label }) => (
            <Link key={to} to={to} className={cn(
              "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors",
              pathname === to ? "bg-primary/15 font-medium text-primary shadow-glow" : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
            )}>
              <Icon className="h-4 w-4 shrink-0" />{label}
            </Link>
          ))}
        </nav>
        <div className="flex items-center justify-between border-t border-border/50 p-3 text-xs text-muted-foreground">
          <span>{APP_VERSION} · 不荐股 · 不预测</span>
          <a href={REPO_URL} target="_blank" rel="noreferrer" className="rounded p-1.5 hover:text-foreground" title="GitHub"><Github className="h-4 w-4" /></a>
        </div>
      </aside>

      {/* Sidebar */}
      <aside className={cn(
        "glass z-10 m-2 hidden shrink-0 flex-col rounded-2xl transition-all duration-200 md:flex",
        collapsed ? "w-14" : "w-60",
      )}>
        {/* Brand */}
        <div className={cn("border-b border-border/50", collapsed ? "flex justify-center p-3" : "p-4")}>
          <Link to="/daily-review" className={cn("flex items-center", collapsed ? "justify-center" : "gap-2")}>
            <LineChart className="h-6 w-6 shrink-0 text-primary text-glow" />
            {!collapsed && (
              <span className="text-lg font-extrabold tracking-tight">
                Vibe-<span className="text-primary">Research</span>
              </span>
            )}
          </Link>
          {!collapsed && <p className="mt-1 text-[11px] text-muted-foreground">个人 AI 投研系统 · A股/美股/港股</p>}
        </div>

        {/* Nav */}
        <nav className={cn("flex-1 space-y-1 overflow-auto", collapsed ? "p-1.5" : "p-2.5")}>
          {NAV.map(({ to, icon: Icon, label }) => {
            const active = pathname === to;
            return (
              <div key={to}>
                <Link
                  to={to}
                  title={collapsed ? label : undefined}
                  className={cn(
                    "flex items-center rounded-lg text-sm transition-colors",
                    collapsed ? "justify-center p-2.5" : "gap-2.5 px-3 py-2.5",
                    active
                      ? "bg-primary/15 font-medium text-primary shadow-glow"
                      : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
                  )}
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  {!collapsed && label}
                </Link>
              </div>
            );
          })}
        </nav>

        {/* Footer */}
        <div className={cn("border-t border-border/50", collapsed ? "flex flex-col items-center gap-2 p-2" : "space-y-2 p-3")}>
          {collapsed ? (
            <>
              <button onClick={toggle} className="rounded p-1.5 text-muted-foreground transition-colors hover:text-foreground" title={dark ? "亮色" : "暗色"}>
                {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              </button>
              <button onClick={() => setCollapsed(false)} className="rounded p-1.5 text-muted-foreground transition-colors hover:text-foreground" title="展开">
                <ChevronsRight className="h-4 w-4" />
              </button>
            </>
          ) : (
            <>
              <div className="flex items-center justify-between">
                <button onClick={toggle} className="flex items-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground">
                  {dark ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
                  {dark ? "亮色" : "暗色"}
                </button>
                <div className="flex items-center gap-2">
                  <a href={REPO_URL} target="_blank" rel="noreferrer" className="text-muted-foreground transition-colors hover:text-foreground" title="GitHub">
                    <Github className="h-3.5 w-3.5" />
                  </a>
                  <button onClick={() => setCollapsed(true)} className="rounded p-1 text-muted-foreground transition-colors hover:text-foreground" title="收起">
                    <ChevronsLeft className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
              <p className="text-[11px] leading-relaxed text-muted-foreground/60">
                {APP_VERSION} · 不荐股 · 不预测 · 无倾向
              </p>
            </>
          )}
        </div>
      </aside>

      {/* Main */}
      <main className="min-w-0 flex-1 overflow-auto pt-14 md:pt-0">
        <div className="mx-auto min-w-0 max-w-6xl px-3 py-4 sm:px-4 md:px-6 md:py-6">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
