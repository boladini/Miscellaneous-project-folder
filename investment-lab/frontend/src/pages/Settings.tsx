import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Button, Card, ErrorState, Kv, Loading, StateBadge, Table, Toast, TypedConfirm } from "../components/ui";
import { ago, dt, pct } from "../format";
import { useApi, useJobWatcher } from "../hooks";

const LIMIT_LABELS: Record<string, [string, "pct" | "num"]> = {
  max_ticker_fraction: ["Max per ticker (of NAV)", "pct"], max_etf_fraction: ["Max per ETF", "pct"], max_sector_fraction: ["Max per sector", "pct"],
  max_experiment_fraction: ["Max per experiment", "pct"], max_binary_event_fraction: ["Max binary-event position", "pct"], min_cash_fraction: ["Min uncommitted cash", "pct"],
  drawdown_circuit_breaker: ["Drawdown circuit breaker", "pct"], min_price: ["Min stock price ($)", "num"], min_avg_dollar_volume_20d: ["Min 20d avg $ volume", "num"],
  slippage_bps: ["Slippage per side (bps)", "num"], order_expiry_sessions: ["Order expiry (sessions)", "num"], max_bar_age_sessions: ["Max quote age (sessions)", "num"],
};
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export default function Settings() {
  const { data, error, loading, reload } = useApi<any>("/settings");
  const status = useApi<any>("/system/status", { poll: 30000 });
  const jobs = useApi<any>("/jobs?limit=30", { poll: 30000 });
  const [form, setForm] = useState<any>(null);
  const [limits, setLimits] = useState<Record<string, string>>({});
  const [toast, setToast] = useState<string | null>(null);
  const [reset, setReset] = useState(false);
  const [busy, setBusy] = useState(false);
  const { job, watch } = useJobWatcher(() => { status.reload(); jobs.reload(); });
  useEffect(() => {
    if (!data) return;
    setForm(JSON.parse(JSON.stringify(data.settings)));
    setLimits(Object.fromEntries(Object.entries(data.risk_policy.limits).map(([k, v]: any) => [k, String(v)])));
  }, [data]);
  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState error={error} onRetry={reload} />;
  if (!form) return <Loading />;
  const s = status.data;

  async function save() {
    setBusy(true);
    try {
      const changed: Record<string, number> = {};
      for (const [k, v] of Object.entries(limits)) if (Number(v) !== Number(data.risk_policy.limits[k])) changed[k] = Number(v);
      const r = await api("/settings", { method: "PATCH", body: {
        timezone: form.timezone, research_schedule: form.research_schedule, min_score: Number(form.min_score), max_proposals_per_run: Number(form.max_proposals_per_run),
        proposal_validity_days: Number(form.proposal_validity_days), notifications: form.notifications, ...(Object.keys(changed).length ? { risk_limits: changed } : {}) } });
      setToast(`Saved. ${r.note}${Object.keys(changed).length ? ` New risk policy v${r.risk_policy_version}.` : ""}`);
      reload();
    } catch (e: any) { setToast(e.message); } finally { setBusy(false); }
  }
  async function post(path: string, body?: any, msg?: string) {
    try { const r = await api(path, { method: "POST", body }); if (r.job_id) watch(r.job_id, r.state); setToast(msg || r.note || "Done."); status.reload(); }
    catch (e: any) { setToast(e.message); }
  }
  const input = "mt-0.5 block w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-800";
  return (
    <div className="space-y-4">
      <div><h1 className="text-xl font-bold">Settings</h1><p className="text-sm text-slate-500">Changes affect future proposals. Already-approved experiments keep their approved scope.</p></div>
      {s && (
        <Card title="System health" actions={<>
          <Button onClick={() => post("/jobs/market-refresh", undefined, "Market data refresh queued.")}>Refresh data</Button>
          <Button onClick={() => post("/jobs/research", undefined, "Research run queued.")}>Run research now</Button>
          {job && <span className="text-xs"><StateBadge state={job.state} /></span>}</>}>
          <Kv items={[["Mode", s.mode === "demo" ? <Badge key="m" tone="warn">demo — synthetic data</Badge> : <Badge key="m" tone="good">live-paper</Badge>],
            ["Market provider", <span key="p">{s.market_provider.name} {s.market_provider.configured ? <Badge tone="good">configured</Badge> : <Badge tone="warn">{s.market_provider.reason}</Badge>}</span>],
            ["Adjustment basis", s.market_provider.adjustment_basis], ["Data expected through", s.data.expected_latest_session],
            ["Latest stored", s.data.latest_stored_session || "none"], ["Stale instruments", s.data.stale.length],
            ["AI model", s.llm.configured ? `${s.llm.provider} · ${s.llm.model}` : "template mode (ANTHROPIC_API_KEY not set)"],
            ["Dead-letter jobs", s.dead_letter_jobs], ["Next daily cycle", dt(s.next_runs.daily)], ["Next research", dt(s.next_runs.research)],
            ["Next monthly review", s.next_runs.monthly_review_date], ["Ledger entries", s.ledger_entries]]} />
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <div className="min-w-0"><div className="mb-1 text-xs font-semibold uppercase text-slate-500">Research sources</div>
              <Table head={["Source", "Channel", "Status"]} rows={s.research_providers.map((r: any) => [r.name, r.channel, r.available ? <Badge key="a" tone="good">available</Badge> : <span key="a"><Badge tone="warn">unavailable</Badge> <span className="text-xs">{r.reason}</span></span>])} /></div>
            <div className="min-w-0"><div className="mb-1 text-xs font-semibold uppercase text-slate-500">Provider calls & quota</div>
              <Table head={["Provider", "State", "Calls today", "Last success", "Last error"]} rows={s.providers.map((p: any) => [p.name, <StateBadge key="s" state={p.state} />, p.calls_today, ago(p.last_success_at), <span key="e" className="text-xs">{p.last_error || "—"}</span>])} empty="No provider calls yet." /></div>
          </div>
          <div className="mt-3 text-xs">Reconciliation: {Object.entries(s.reconciliation).map(([k, v]: any) => <span key={k} className="mr-2">{k} {v.ok ? <Badge tone="good">ok</Badge> : <Badge tone="bad">issue</Badge>}</span>)}</div>
        </Card>
      )}
      <Card title="Run history">
        <Table head={["Job", "Key", "State", "Attempts", "Trigger", "Started", "Finished", "Error"]} rows={(jobs.data?.items || []).map((j: any) => [j.type, <code key="k" className="text-xs">{j.schedule_key}</code>, <StateBadge key="s" state={j.state} />,
          `${j.attempts}/${j.max_attempts}`, j.trigger, dt(j.started_at), dt(j.finished_at), <span key="e" className="text-xs text-rose-700">{j.error?.split("\n")[0] || j.progress?.step || ""}</span>])} empty="No jobs have run yet. Start the worker process." />
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Schedules & proposals">
          <div className="grid grid-cols-2 gap-3">
            <label className="text-xs">Research day<select className={input} value={form.research_schedule.weekday} onChange={(e) => setForm({ ...form, research_schedule: { ...form.research_schedule, weekday: Number(e.target.value) } })}>
              {DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}</select></label>
            <label className="text-xs">Research hour (local)<input type="number" min={0} max={23} className={input} value={form.research_schedule.hour} onChange={(e) => setForm({ ...form, research_schedule: { ...form.research_schedule, hour: Number(e.target.value) } })} /></label>
            <label className="text-xs">Timezone<input className={input} value={form.timezone} onChange={(e) => setForm({ ...form, timezone: e.target.value })} /></label>
            <label className="text-xs">Minimum score<input type="number" min={0} max={100} className={input} value={form.min_score} onChange={(e) => setForm({ ...form, min_score: e.target.value })} /></label>
            <label className="text-xs">Max proposals / run<input type="number" min={0} max={3} className={input} value={form.max_proposals_per_run} onChange={(e) => setForm({ ...form, max_proposals_per_run: e.target.value })} /></label>
            <label className="text-xs">Proposal validity (days)<input type="number" min={1} max={30} className={input} value={form.proposal_validity_days} onChange={(e) => setForm({ ...form, proposal_validity_days: e.target.value })} /></label>
          </div>
          <p className="mt-2 text-xs text-slate-500">Daily cycle runs after finalized end-of-day data (≈6:30 PM ET) each trading day. Monthly review: first trading Monday.</p>
        </Card>
        <Card title="Notifications">
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!form.notifications.email_enabled} onChange={(e) => setForm({ ...form, notifications: { ...form.notifications, email_enabled: e.target.checked } })} /> Email urgent alerts</label>
          <label className="mt-2 block text-xs">Email address<input type="email" className={input} value={form.notifications.email_to || ""} onChange={(e) => setForm({ ...form, notifications: { ...form.notifications, email_to: e.target.value } })} /></label>
          <label className="mt-2 flex items-center gap-2 text-sm"><input type="checkbox" checked={form.notifications.digest_routine_fills !== false} onChange={(e) => setForm({ ...form, notifications: { ...form.notifications, digest_routine_fills: e.target.checked } })} /> Routine fills as digest</label>
          <p className="mt-2 text-xs text-slate-500">Email requires SMTP settings on the server ({data.providers.smtp_configured ? "configured" : "not configured"}). In-app notifications are always on.</p>
        </Card>
      </div>
      <Card title={`Risk defaults (policy v${data.risk_policy.version})`}>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {Object.entries(LIMIT_LABELS).map(([k, [label, kind]]) => (
            <label key={k} className="text-xs">{label}{kind === "pct" && <span className="text-slate-400"> (fraction, e.g. 0.10 = {pct(Number(limits[k] || 0), 0, false)})</span>}
              <input type="number" step="any" className={input} value={limits[k] ?? ""} onChange={(e) => setLimits({ ...limits, [k]: e.target.value })} /></label>))}
        </div>
        <p className="mt-2 text-xs text-slate-500">Saving creates a new immutable policy version. Stricter limits approved in a strategy always prevail.</p>
      </Card>
      <div className="flex justify-end"><Button variant="primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save settings"}</Button></div>
      <Card title="Providers & credentials">
        <Kv items={[["Market provider (env MARKET_PROVIDER)", data.providers.market_provider], ["Tiingo key", data.providers.tiingo_key_configured ? "configured" : "not set"],
          ["Alpha Vantage key", data.providers.alphavantage_key_configured ? "configured" : "not set"], ["Anthropic key", data.providers.anthropic_key_configured ? `configured (${data.providers.llm_model})` : "not set"],
          ["SEC user agent", data.providers.sec_user_agent_configured ? "configured" : "not set"], ["Reddit API", data.providers.reddit_configured ? "configured" : "not set"],
          ["X API", data.providers.x_configured ? "configured" : "not set"], ["Benchmark", data.providers.benchmark], ["Universe", `${data.providers.universe.length} symbols`]]} />
        <p className="mt-2 text-xs text-slate-500">{data.providers.note} See README → Configuration.</p>
      </Card>
      <Card title="Data & administration">
        <div className="flex flex-wrap gap-2">
          <a className="inline-flex min-h-[40px] items-center rounded-lg bg-white px-3 text-sm font-semibold ring-1 ring-slate-300 dark:bg-slate-800 dark:ring-slate-700" href="/api/v1/export?format=json">Export all (JSON)</a>
          {["ledger", "orders", "lots", "valuations", "evidence", "strategy_versions", "approvals"].map((t) => (
            <a key={t} className="inline-flex min-h-[40px] items-center rounded-lg bg-white px-3 text-sm ring-1 ring-slate-300 dark:bg-slate-800 dark:ring-slate-700" href={`/api/v1/export?format=csv&table=${t}`}>{t} CSV</a>))}
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant={s?.global_pause ? "primary" : "danger"} onClick={() => post("/admin/global-pause", { paused: !s?.global_pause }, s?.global_pause ? "Global pause lifted." : "Emergency pause ON: no new entries anywhere; exits continue.")}>
            {s?.global_pause ? "Lift emergency pause" : "Emergency pause all entries"}</Button>
          <Button variant="danger" onClick={() => setReset(true)}>Reset lab…</Button>
        </div>
      </Card>
      <TypedConfirm open={reset} title="Reset the lab?" word="RESET" onClose={() => setReset(false)} onConfirm={() => { setReset(false); post("/admin/reset", { confirm: "RESET" }, "Lab reset: portfolios archived and a new inception started."); }}
        body={<>All current portfolios and experiments are <strong>archived</strong> (never deleted) and five fresh $10,000 portfolios start a new tracked inception. Pending proposals are withdrawn.</>} />
      <Toast message={toast} onClose={() => setToast(null)} tone="info" />
    </div>
  );
}
