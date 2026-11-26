import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { MoreLinks } from "../App";
import { EquityChart, Sparkline } from "../components/charts";
import { Badge, Card, Empty, ErrorState, Loading, RangeFilter, rangeStart, Stat, StateBadge } from "../components/ui";
import { ago, dt, familyLabel, pct, signClass, usd } from "../format";
import { useApi } from "../hooks";

export default function Dashboard() {
  const [range, setRange] = useState("ALL");
  const start = rangeStart(range);
  const { data, error, loading, reload } = useApi<any>(`/dashboard${start ? `?start=${start}` : ""}`, { poll: 30000 });
  const nav = useNavigate();
  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState error={error} onRetry={reload} />;
  const d = data!;
  const totalRet = d.aggregate_starting_cash ? d.aggregate_nav / d.aggregate_starting_cash - 1 : null;
  const last = d.series[d.series.length - 1];
  const benchRet = last?.benchmark_nav ? last.benchmark_nav / d.aggregate_starting_cash - 1 : null;
  return (
    <div className="space-y-4">
      <h1 className="sr-only">Dashboard</h1>
      <MoreLinks />
      {d.pending_approvals > 0 ? (
        <Link to="/approvals" className="block rounded-xl bg-blue-600 p-5 text-white shadow-sm transition hover:bg-blue-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2">
          <div className="text-xs font-semibold uppercase tracking-wider opacity-90">Your decision needed</div>
          <div className="mt-1 text-2xl font-bold">{d.pending_approvals} strateg{d.pending_approvals === 1 ? "y" : "ies"} awaiting Yes / No</div>
          <div className="mt-1 text-sm opacity-90">Review the thesis, evidence and rules. Typical review time: under two minutes each. →</div>
        </Link>
      ) : (
        <div className="rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">
          ✓ No decisions pending. Next research run {ago(d.system.next_runs.research)} ({dt(d.system.next_runs.research)}). Zero proposals in a week is a valid outcome.
        </div>
      )}
      <HealthStrip s={d.system} />
      <Card title="All portfolios vs benchmark" actions={<RangeFilter value={range} onChange={setRange} />}>
        <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="Aggregate NAV" value={usd(d.aggregate_nav)} sub={`of ${usd(d.aggregate_starting_cash, 0)} virtual capital`} tip="NAV = cash + positions marked at the latest finalized close, summed over the five independent portfolios." />
          <Stat label="All-time return" value={pct(totalRet)} valueClass={signClass(totalRet)} />
          <Stat label="Benchmark (shadow)" value={pct(benchRet)} sub="Same capital & inception, price-only" tip="Each portfolio has a shadow benchmark account started with the same capital at the same inception. It does not consume the $50,000." />
          <Stat label="Excess" value={pct(totalRet !== null && benchRet !== null ? totalRet - benchRet : null)} valueClass={signClass(totalRet !== null && benchRet !== null ? totalRet - benchRet : null)} />
        </div>
        <EquityChart data={d.series} aName="Aggregate NAV" bName="Aggregate benchmark (price-only)" />
      </Card>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        {d.portfolios.map((p: any) => (
          <button key={p.id} onClick={() => nav(`/portfolios/${p.id}`)} className="rounded-xl border border-slate-200 bg-white p-4 text-left shadow-sm transition hover:border-blue-300 hover:shadow focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 dark:border-slate-800 dark:bg-slate-900">
            <div className="flex items-start justify-between gap-2">
              <div className="text-sm font-semibold">{familyLabel[p.family] || p.name}</div>
              {p.circuit_breaker ? <Badge tone="bad">breaker</Badge> : p.status !== "active" ? <StateBadge state={p.status} /> : null}
            </div>
            <div className="mt-2 text-lg font-bold tabular-nums">{usd(p.nav)}</div>
            <div className="flex gap-3 text-xs">
              <span className={signClass(p.all_time_return)}>{pct(p.all_time_return)}</span>
              <span className="text-slate-500">vs bench {pct(p.metrics.benchmark_return)}</span>
            </div>
            <div className="mt-2"><Sparkline values={p.sparkline} label={`${p.name} NAV trend`} /></div>
            <div className="mt-2 text-xs text-slate-500">
              <div>{p.experiments_active} active experiment{p.experiments_active === 1 ? "" : "s"} · {p.positions} position{p.positions === 1 ? "" : "s"}</div>
              <div>Cash {usd(p.cash, 0)}</div>
            </div>
            {p.valuation_quality !== "ok" && <div className="mt-2"><Badge tone="warn">valuation unresolved</Badge></div>}
          </button>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Recent activity" actions={<Link to="/journal" className="text-xs text-blue-700 underline dark:text-blue-400">Full timeline</Link>}>
          {d.activity.length ? (
            <ul className="space-y-2 text-sm">
              {d.activity.map((a: any, i: number) => (
                <li key={i} className="flex justify-between gap-3">
                  <span><span className="font-medium">{a.action}</span> <span className="text-slate-500">by {a.actor}</span></span>
                  <time className="shrink-0 text-xs text-slate-500" dateTime={a.at}>{ago(a.at)}</time>
                </li>
              ))}
            </ul>
          ) : <Empty title="No activity yet" />}
        </Card>
        <SystemCard s={d.system} />
      </div>
    </div>
  );
}

function HealthStrip({ s }: { s: any }) {
  const issues: string[] = [];
  if (!s.market_provider.configured) issues.push(`Market data not configured: ${s.market_provider.reason}`);
  if (s.data.stale?.length) issues.push(`${s.data.stale.length} instrument(s) have stale data`);
  if (s.dead_letter_jobs) issues.push(`${s.dead_letter_jobs} job(s) failed permanently`);
  if (s.global_pause) issues.push("Emergency pause is ON — no new entries");
  const lastDaily = s.jobs.daily?.last_success_at;
  if (!lastDaily) issues.push("No daily cycle has completed yet — is the worker running?");
  if (!issues.length) return null;
  return (
    <div role="alert" className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
      <strong>! Attention:</strong> {issues.join(" · ")} <Link to="/settings" className="underline">Details</Link>
    </div>
  );
}

function SystemCard({ s }: { s: any }) {
  return (
    <Card title="System health" actions={<Link to="/settings" className="text-xs text-blue-700 underline dark:text-blue-400">Settings</Link>}>
      <ul className="space-y-2 text-sm">
        <li className="flex justify-between gap-2"><span>Mode</span><Badge tone={s.mode === "demo" ? "warn" : "good"}>{s.mode === "demo" ? "demo (synthetic)" : "live-paper"}</Badge></li>
        <li className="flex justify-between gap-2"><span>Market data</span><span className="text-right">{s.market_provider.name} {s.market_provider.configured ? <Badge tone="good">configured</Badge> : <Badge tone="warn">not configured</Badge>}</span></li>
        <li className="flex justify-between gap-2"><span>Latest stored session</span><span>{s.data.latest_stored_session || "none"} {s.data.latest_stored_session && s.data.latest_stored_session < s.data.expected_latest_session ? <Badge tone="warn">stale</Badge> : null}</span></li>
        <li className="flex justify-between gap-2"><span>AI model</span><span>{s.llm.configured ? `${s.llm.provider} · ${s.llm.model}` : <Badge tone="warn">template mode (no LLM key)</Badge>}</span></li>
        {["daily", "research", "monthly"].map((k) => (
          <li key={k} className="flex justify-between gap-2"><span className="capitalize">Last {k} run</span>
            <span>{s.jobs[k]?.last_success_at ? ago(s.jobs[k].last_success_at) : "never"} {s.jobs[k]?.last_state && s.jobs[k].last_state !== "succeeded" ? <StateBadge state={s.jobs[k].last_state} /> : null}</span></li>
        ))}
        <li className="flex justify-between gap-2"><span>Next daily cycle</span><span>{dt(s.next_runs.daily)}</span></li>
      </ul>
    </Card>
  );
}
