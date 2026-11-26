import { createContext, useContext, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api } from "./api";
import { useApi } from "./hooks";
import { Button, Loading } from "./components/ui";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Approvals from "./pages/Approvals";
import { PortfolioDetail, Portfolios } from "./pages/Portfolios";
import ExperimentDetail from "./pages/Experiment";
import Research from "./pages/Research";
import Backtests from "./pages/Backtests";
import Journal from "./pages/Journal";
import Notifications from "./pages/Notifications";
import Settings from "./pages/Settings";

type Me = { user: { id: string; email: string }; mode: string; paper_trading_only: boolean };
const MeCtx = createContext<Me | null>(null);
export const useMe = () => useContext(MeCtx);

const NAV = [
  { to: "/dashboard", label: "Dashboard", icon: "▦" },
  { to: "/approvals", label: "Approvals", icon: "✓" },
  { to: "/portfolios", label: "Portfolios", icon: "◧" },
  { to: "/research", label: "Research", icon: "⌕" },
  { to: "/backtests", label: "Backtests", icon: "↺" },
  { to: "/journal", label: "Journal", icon: "✎" },
  { to: "/notifications", label: "Alerts", icon: "◔" },
  { to: "/settings", label: "Settings", icon: "⚙" },
];
const MOBILE_NAV = ["/dashboard", "/approvals", "/portfolios", "/notifications", "/settings"];

export default function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  useEffect(() => {
    api<Me>("/auth/me").then(setMe).catch(() => setMe(null));
    const h = () => setMe(null);
    window.addEventListener("lab:unauthenticated", h);
    return () => window.removeEventListener("lab:unauthenticated", h);
  }, []);
  if (me === undefined) return <Loading label="Connecting…" />;
  if (me === null) return <Login onLogin={() => api<Me>("/auth/me").then(setMe)} />;
  return (
    <MeCtx.Provider value={me}>
      <Shell onLogout={async () => { await api("/auth/logout", { method: "POST" }).catch(() => {}); setMe(null); }} />
    </MeCtx.Provider>
  );
}

function Shell({ onLogout }: { onLogout: () => void }) {
  const me = useMe()!;
  const counts = useApi<any>("/notifications?unread=true&limit=1", { poll: 60000 });
  const pending = useApi<any>("/proposals?status=pending&limit=1", { poll: 60000 });
  const loc = useLocation();
  useEffect(() => { window.scrollTo(0, 0); }, [loc.pathname]);
  const badge = (to: string) => {
    const n = to === "/notifications" ? counts.data?.unread : to === "/approvals" ? pending.data?.total : 0;
    return n ? <span className="ml-auto rounded-full bg-blue-600 px-1.5 text-[11px] font-bold text-white" aria-label={`${n} new`}>{n}</span> : null;
  };
  return (
    <div className="min-h-screen md:flex">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2">Skip to content</a>
      <aside className="hidden w-60 shrink-0 flex-col border-r border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 md:flex">
        <div className="px-5 py-5">
          <div className="text-base font-bold tracking-tight">Investment Lab</div>
          <div className="text-xs text-slate-500">Autonomous research · paper trading</div>
        </div>
        <nav aria-label="Primary" className="flex-1 space-y-0.5 px-3">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} className={({ isActive }) => `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium ${isActive ? "bg-blue-50 text-blue-800 dark:bg-blue-950 dark:text-blue-200" : "text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"}`}>
              <span aria-hidden="true" className="w-4 text-center">{n.icon}</span>{n.label}{badge(n.to)}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-slate-200 p-4 text-xs text-slate-500 dark:border-slate-800">
          <div className="truncate">{me.user.email}</div>
          <Button variant="ghost" className="mt-1 -ml-3" onClick={onLogout}>Sign out</Button>
        </div>
      </aside>
      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/90 backdrop-blur dark:border-slate-800 dark:bg-slate-900/90">
          <div className="flex items-center gap-2 px-4 py-2">
            <span className="font-bold md:hidden">Investment Lab</span>
            <span className="ml-auto inline-flex items-center gap-1 rounded-md bg-amber-100 px-2 py-1 text-xs font-bold tracking-wide text-amber-900 ring-1 ring-amber-300 dark:bg-amber-950 dark:text-amber-200 dark:ring-amber-800"
              title="All trades are simulated. No brokerage is connected and no real orders can be placed.">
              <span aria-hidden="true">⚠</span> PAPER TRADING
            </span>
            <button onClick={onLogout} className="text-xs text-slate-500 underline md:hidden">Sign out</button>
          </div>
          {me.mode === "demo" && (
            <div role="note" className="border-t border-amber-200 bg-amber-50 px-4 py-1.5 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/60 dark:text-amber-200">
              <strong>Demo mode.</strong> Prices, events and research sources are deterministic <strong>synthetic</strong> data (symbols prefixed “SYN”, sources marked [SYNTHETIC]). Configure live providers in Settings for real observations.
            </div>
          )}
        </header>
        <main id="main" className="mx-auto max-w-7xl px-4 pb-24 pt-4 md:pb-10">
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/approvals" element={<Approvals />} />
            <Route path="/portfolios" element={<Portfolios />} />
            <Route path="/portfolios/:id" element={<PortfolioDetail />} />
            <Route path="/experiments/:id" element={<ExperimentDetail />} />
            <Route path="/research" element={<Research />} />
            <Route path="/backtests" element={<Backtests />} />
            <Route path="/journal" element={<Journal />} />
            <Route path="/notifications" element={<Notifications />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<div className="p-6">Page not found. <NavLink className="text-blue-700 underline" to="/dashboard">Go to dashboard</NavLink></div>} />
          </Routes>
        </main>
        <nav aria-label="Mobile" className="fixed inset-x-0 bottom-0 z-40 grid grid-cols-5 border-t border-slate-200 bg-white pb-[env(safe-area-inset-bottom)] dark:border-slate-800 dark:bg-slate-900 md:hidden">
          {NAV.filter((n) => MOBILE_NAV.includes(n.to)).map((n) => (
            <NavLink key={n.to} to={n.to} className={({ isActive }) => `relative flex flex-col items-center py-2 text-[11px] font-medium ${isActive ? "text-blue-700 dark:text-blue-400" : "text-slate-600 dark:text-slate-400"}`}>
              <span aria-hidden="true" className="text-lg leading-none">{n.icon}</span>{n.label}
              <span className="absolute right-3 top-1">{badge(n.to)}</span>
            </NavLink>
          ))}
        </nav>
      </div>
    </div>
  );
}

export function MoreLinks() {
  return (
    <div className="flex flex-wrap gap-2 md:hidden">
      {NAV.filter((n) => !MOBILE_NAV.includes(n.to)).map((n) => (
        <NavLink key={n.to} to={n.to} className="rounded-lg bg-white px-3 py-2 text-sm font-medium ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-800">{n.icon} {n.label}</NavLink>
      ))}
    </div>
  );
}
