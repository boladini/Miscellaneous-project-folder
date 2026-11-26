import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { ComparisonChart, DrawdownChart, EquityChart } from "../components/charts";
import { Badge, Button, Card, ErrorState, Loading, Modal, RangeFilter, rangeStart, Stat, StateBadge, Table, Tabs, Toast } from "../components/ui";
import { day, familyLabel, num, pct, signClass, usd } from "../format";
import { useApi } from "../hooks";

export function Portfolios() {
  const [range, setRange] = useState("ALL");
  const [family, setFamily] = useState("");
  const start = rangeStart(range);
  const qs = new URLSearchParams();
  if (start) qs.set("start", start);
  if (family) qs.set("family", family);
  const { data, error, loading, reload } = useApi<any>(`/portfolios?${qs}`);
  const [series, setSeries] = useState<any[]>([]);
  useEffect(() => {
    if (!data) return;
    Promise.all(data.leaderboard.map((p: any) => api(`/portfolios/${p.id}/series${start ? `?start=${start}` : ""}`).then((r) => ({ name: familyLabel[p.family], points: r.series }))))
      .then(setSeries).catch(() => setSeries([]));
  }, [data, start]);
  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState error={error} onRetry={reload} />;
  const d = data!;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-xl font-bold">Portfolios</h1><p className="text-sm text-slate-500">Five independent $10,000 paper portfolios, each with a matched benchmark shadow account.</p></div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="sr-only" htmlFor="fam">Strategy family</label>
          <select id="fam" value={family} onChange={(e) => setFamily(e.target.value)} className="rounded-md border border-slate-300 px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-800">
            <option value="">All families</option>{Object.entries(familyLabel).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <RangeFilter value={range} onChange={setRange} />
        </div>
      </div>
      <Card title="Leaderboard — return vs benchmark">
        <Table caption="Portfolio leaderboard" head={["#", "Portfolio", "NAV", "Return", "Benchmark", "Excess", "Max DD", "Sharpe", "Sample", "Status"]}
          rows={d.leaderboard.map((p: any, i: number) => [
            p.metrics.excess_return === null ? "—" : i + 1,
            <Link key="l" to={`/portfolios/${p.id}`} className="font-medium text-blue-700 underline dark:text-blue-400">{familyLabel[p.family]}</Link>,
            usd(p.nav), <span key="r" className={signClass(p.metrics.total_return)}>{pct(p.metrics.total_return)}</span>, pct(p.metrics.benchmark_return),
            <span key="e" className={signClass(p.metrics.excess_return)}>{pct(p.metrics.excess_return)}</span>, pct(p.metrics.max_drawdown), num(p.metrics.sharpe),
            <span key="s">{p.metrics.observations} sessions · {p.closed_trades} trades{p.metrics.insufficient_history && <span className="block"><Badge tone="warn">insufficient history</Badge></span>}</span>,
            p.circuit_breaker ? <Badge key="b" tone="bad">breaker</Badge> : <StateBadge key="b" state={p.status} />,
          ])} />
        <p className="mt-2 text-xs text-slate-500">{d.note} Benchmark: price-only shadow account with matched capital and inception.</p>
      </Card>
      <Card title="Comparison (indexed to 100 at range start)"><ComparisonChart series={series} /></Card>
      <Card title="Experiments">
        <Table caption="Experiments" head={["Experiment", "Portfolio", "Status", "Thesis", "Budget", "P&L", "Return on budget", "Trades", "Age"]}
          rows={d.experiments.map((e: any) => [
            <Link key="l" to={`/experiments/${e.id}`} className="font-medium text-blue-700 underline dark:text-blue-400">{e.name}</Link>, familyLabel[e.portfolio_family],
            <StateBadge key="s" state={e.status} />, <StateBadge key="t" state={e.thesis_status} />, usd(e.budget, 0),
            <span key="p" className={signClass(e.total_pnl)}>{usd(e.total_pnl)}</span>, pct(e.return_on_budget),
            <span key="n">{e.closed_trades}{e.insufficient_history && <span className="ml-1 text-xs text-slate-500">(too few)</span>}</span>, `${e.age_days}d`,
          ])} empty="No experiments yet — approve a proposal to start one." />
      </Card>
    </div>
  );
}

export function PortfolioDetail() {
  const { id } = useParams();
  const [range, setRange] = useState("ALL");
  const start = rangeStart(range);
  const { data, error, loading, reload } = useApi<any>(`/portfolios/${id}${start ? `?start=${start}` : ""}`, { poll: 60000 });
  const series = useApi<any>(`/portfolios/${id}/series${start ? `?start=${start}` : ""}`);
  const [tab, setTab] = useState("positions");
  const [confirm, setConfirm] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState error={error} onRetry={reload} />;
  const d = data!;
  const p = d.portfolio;
  const paused = p.status === "paused" || p.circuit_breaker;
  async function togglePause() {
    setBusy(true);
    try {
      await api(`/portfolios/${id}/${paused ? "resume" : "pause"}`, { method: "POST", body: paused ? undefined : { reason: "paused from dashboard" } });
      setToast(paused ? "Resumed: new entries may occur under approved rules." : "Paused: no new entries; approved exits continue.");
      setConfirm(false);
      reload();
    } catch (e: any) { setToast(e.message); } finally { setBusy(false); }
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link to="/portfolios" className="text-xs text-blue-700 underline dark:text-blue-400">← Portfolios</Link>
          <h1 className="text-xl font-bold">{familyLabel[p.family]} portfolio</h1>
          <div className="mt-1 flex flex-wrap gap-2"><StateBadge state={p.status} />{p.circuit_breaker && <Badge tone="bad">drawdown circuit breaker</Badge>}
            {p.valuation_quality !== "ok" && <Badge tone="warn">valuation unresolved</Badge>}<span className="text-xs text-slate-500">inception {day(p.inception_at)}</span></div>
          {p.paused_reason && <p className="mt-1 text-xs text-slate-500">{p.paused_reason}</p>}
        </div>
        <div className="flex items-center gap-2"><RangeFilter value={range} onChange={setRange} />
          <Button variant={paused ? "primary" : "secondary"} onClick={() => setConfirm(true)}>{paused ? "Resume entries" : "Pause entries"}</Button></div>
      </div>
      <Card>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="NAV" value={usd(p.nav)} tip="Cash + positions at the latest finalized close." />
          <Stat label="Cash" value={usd(p.cash)} sub={p.reserved_cash ? `${usd(p.reserved_cash)} reserved for pending buys` : undefined} />
          <Stat label="Holdings" value={usd(p.holdings_value)} sub={`exposure ${pct(p.exposure, 0, false)}`} />
          <Stat label="Realized P&L" value={usd(p.realized_pnl)} valueClass={signClass(p.realized_pnl)} />
          <Stat label="Unrealized P&L" value={usd(p.unrealized_pnl)} valueClass={signClass(p.unrealized_pnl)} />
          <Stat label="vs benchmark" value={pct(p.metrics.excess_return)} valueClass={signClass(p.metrics.excess_return)} sub={`daily ${pct(p.daily_return)} · DD ${pct(p.drawdown)}`} />
        </div>
      </Card>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Equity vs benchmark" className="lg:col-span-2"><EquityChart data={series.data?.series || []} /></Card>
        <Card title="Drawdown from peak"><DrawdownChart data={series.data?.series || []} />
          <p className="mt-2 text-xs text-slate-500">Circuit breaker pauses new entries at −15% from peak (default). Benchmark: {d.benchmark?.symbol}, {d.benchmark?.basis}{d.benchmark?.entry_session ? `, bought ${day(d.benchmark.entry_session)}` : ", awaiting first eligible open"}.</p>
          <p className="mt-1 text-xs">{d.reconciliation.ok ? <Badge tone="good">ledger reconciles</Badge> : <Badge tone="bad">reconciliation issue</Badge>}</p></Card>
      </div>
      <Tabs label="Portfolio details" value={tab} onChange={setTab} tabs={[{ id: "positions", label: `Positions (${d.positions.length})` }, { id: "experiments", label: "Experiments" },
        { id: "trades", label: "Orders & fills" }, { id: "closed", label: "Closed trades" }, { id: "attr", label: "Attribution" }]} />
      {tab === "positions" && <Card><Table caption="Open positions" head={["Symbol", "Qty", "Entry", "Last", "Value", "Unrealized", "Opened", "Status"]} rows={d.positions.map((x: any) => [
        x.symbol, num(x.quantity, 4), usd(x.entry_price), <span key="l">{usd(x.last_price)}<span className="block text-[11px] text-slate-500">{x.price_session}</span></span>, usd(x.market_value),
        <span key="u" className={signClass(x.unrealized_pnl)}>{usd(x.unrealized_pnl)}</span>, day(x.opened_session),
        x.unresolved_valuation ? <Badge key="s" tone="bad">unresolved</Badge> : <StateBadge key="s" state={x.status} />])} empty="No open positions. Cash only — nothing fabricated." /></Card>}
      {tab === "experiments" && <Card><Table caption="Experiments" head={["Experiment", "Status", "Thesis", "Budget", "P&L", "Trades"]} rows={d.experiments.map((e: any) => [
        <Link key="l" className="text-blue-700 underline dark:text-blue-400" to={`/experiments/${e.id}`}>{e.name}</Link>, <StateBadge key="s" state={e.status} />, <StateBadge key="t" state={e.thesis_status} />,
        usd(e.budget, 0), <span key="p" className={signClass(e.total_pnl)}>{usd(e.total_pnl)}</span>, e.closed_trades])} empty="No experiments in this portfolio." /></Card>}
      {tab === "trades" && <Card><TradesTable trades={d.trades} /></Card>}
      {tab === "closed" && <Card><ClosedTable lots={d.closed_lots} /></Card>}
      {tab === "attr" && <Card><div className="grid gap-4 md:grid-cols-3">{(["by_experiment", "by_ticker", "by_holding_period_closed"] as const).map((k) => (
        <div key={k}><div className="mb-1 text-xs font-semibold uppercase text-slate-500">{k.replace(/_/g, " ")}</div>
          <Table head={["Key", "P&L"]} rows={Object.entries(d.attribution[k]).map(([a, v]: any) => [a, <span key="v" className={signClass(v)}>{usd(v)}</span>])} empty="No P&L yet." /></div>))}</div></Card>}
      <Modal open={confirm} title={paused ? "Resume new entries?" : "Pause new entries?"} onClose={() => setConfirm(false)}>
        <p className="text-sm">{paused ? (p.circuit_breaker ? "The drawdown circuit breaker tripped. Resuming is your explicit approval to allow new entries again; the peak resets to current NAV." : "New entries will resume under approved rules.") : "No new positions will be opened in this portfolio. Approved exits continue. No forced liquidation."}</p>
        <div className="mt-4 flex justify-end gap-2"><Button onClick={() => setConfirm(false)}>Cancel</Button><Button variant="primary" disabled={busy} onClick={togglePause}>Confirm</Button></div>
      </Modal>
      <Toast message={toast} onClose={() => setToast(null)} />
    </div>
  );
}

export function TradesTable({ trades }: { trades: any[] }) {
  return <Table caption="Orders and simulated fills" head={["Side", "Symbol", "Qty", "Status", "Signal", "Eligible", "Fill", "Fill price", "Slippage", "Reason"]} rows={trades.map((t) => [
    <Badge key="s" tone={t.side === "buy" ? "info" : "accent"} icon={false}>{t.side}</Badge>, t.symbol, num(t.quantity, 4),
    <span key="st"><StateBadge state={t.status} />{t.recorded_late && <span className="block"><Badge tone="warn">recorded late</Badge></span>}</span>,
    day(t.signal_session), day(t.eligible_session), t.fill_session ? day(t.fill_session) : "—", t.fill_price ? usd(t.fill_price) : "—",
    t.fill_price ? `${t.slippage_bps} bps (${usd(t.slippage_amount)})` : "—",
    <span key="r" className="text-xs">{t.reason}{t.status_reason && <span className="block text-slate-500">{t.status_reason}</span>}</span>])} empty="No orders yet." />;
}

export function ClosedTable({ lots }: { lots: any[] }) {
  return <Table caption="Closed trades" head={["Symbol", "Qty", "Entry", "Exit", "Opened", "Closed", "Realized", "Return"]} rows={lots.map((l) => [
    l.symbol, num(l.quantity, 4), usd(l.entry_price), usd(l.exit_price), day(l.opened_session), day(l.closed_session),
    <span key="p" className={signClass(l.realized_pnl)}>{usd(l.realized_pnl + l.dividends)}</span>, <span key="r" className={signClass(l.return)}>{pct(l.return)}</span>])} empty="No closed trades yet." />;
}
