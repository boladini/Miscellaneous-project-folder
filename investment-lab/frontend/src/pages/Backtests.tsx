import { useState } from "react";
import { api } from "../api";
import { Badge, Button, Card, ErrorState, Kv, Loading, StateBadge, Table, Toast } from "../components/ui";
import { day, familyLabel, num, pct } from "../format";
import { useApi } from "../hooks";
import { BacktestPanel } from "./Approvals";

export default function Backtests() {
  const { data, error, loading, reload } = useApi<any>("/backtests?limit=100");
  const [sel, setSel] = useState<string | null>(null);
  const [cmp, setCmp] = useState<string[]>([]);
  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState error={error} onRetry={reload} />;
  const items = data?.items || [];
  const m = (b: any, seg = "full") => b.metrics?.metrics?.[seg] || {};
  const compared = items.filter((b: any) => cmp.includes(b.id));
  return (
    <div className="space-y-4">
      <div><h1 className="text-xl font-bold">Backtests</h1><p className="text-sm text-slate-500">Deterministic historical tests using the same rule interpreter and execution model as paper trading. Backtests have no trade authority.</p></div>
      <Card title="Saved runs">
        <Table caption="Backtest runs" head={["Compare", "Strategy", "Family", "Status", "Return", "vs bench", "Sharpe", "Max DD", "Trades", "Robust", "Run", ""]} rows={items.map((b: any) => [
          <input key="c" type="checkbox" aria-label={`Compare ${b.strategy_name}`} checked={cmp.includes(b.id)} onChange={(e) => setCmp(e.target.checked ? [...cmp, b.id].slice(-3) : cmp.filter((x) => x !== b.id))} />,
          <span key="n" className="font-medium">{b.strategy_name} <span className="text-xs text-slate-500">v{b.version}</span></span>, familyLabel[b.family] || b.family, <StateBadge key="s" state={b.status === "completed" ? "succeeded" : b.status} />,
          pct(m(b).total_return), pct(m(b).excess_return), num(m(b).sharpe), pct(m(b).max_drawdown), m(b).trade_count ?? "—",
          b.status === "completed" ? <Badge key="r" tone={b.metrics?.robust ? "good" : "warn"}>{b.metrics?.robust ? "robust" : "not robust"}</Badge> : "—",
          <span key="t" className="text-xs">{day(b.created_at)} · {b.trigger}</span>,
          <button key="o" className="text-blue-700 underline dark:text-blue-400" onClick={() => setSel(sel === b.id ? null : b.id)}>{sel === b.id ? "Hide" : "Open"}</button>])} empty="No backtests yet." />
      </Card>
      {compared.length >= 2 && (
        <Card title="Comparison">
          <Table head={["Metric", ...compared.map((b: any) => b.strategy_name)]} rows={[
            ["Return (full)", ...compared.map((b: any) => pct(m(b).total_return))], ["Excess vs benchmark", ...compared.map((b: any) => pct(m(b).excess_return))],
            ["Holdout return", ...compared.map((b: any) => pct(m(b, "holdout").total_return))], ["Stress-cost return", ...compared.map((b: any) => pct(m(b, "stress_cost").total_return))],
            ["Sharpe (rf=0)", ...compared.map((b: any) => num(m(b).sharpe))], ["Max drawdown", ...compared.map((b: any) => pct(m(b).max_drawdown))],
            ["Trades", ...compared.map((b: any) => m(b).trade_count ?? "—")], ["Win rate", ...compared.map((b: any) => pct(m(b).win_rate, 0, false))],
            ["Turnover (x/yr)", ...compared.map((b: any) => num(m(b).turnover))], ["Avg exposure", ...compared.map((b: any) => pct(m(b).avg_exposure, 0, false))]]} />
        </Card>
      )}
      {sel && <Detail id={sel} />}
    </div>
  );
}

function Detail({ id }: { id: string }) {
  const { data, error, loading, reload } = useApi<any>(`/backtests/${id}`);
  const [toast, setToast] = useState<string | null>(null);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  const d = data!;
  async function rerun() {
    try { const r = await api("/backtests", { method: "POST", body: { strategy_version_id: d.strategy_version_id } }); setToast(`Rerun queued (job ${r.job_id.slice(0, 8)}). It will appear here when finished.`); }
    catch (e: any) { setToast(e.message); }
  }
  return (
    <Card title={`${d.strategy_name} · v${d.version}`} actions={<Button onClick={rerun}>Rerun on current data</Button>}>
      <BacktestPanel bt={d} />
      <div className="mt-4">
        <div className="mb-1 text-xs font-semibold uppercase text-slate-500">Reproducibility manifest</div>
        <Kv items={[["Rule hash", <code key="r" className="text-xs">{d.reproduce.rule_hash.slice(0, 20)}…</code>], ["Code version", d.reproduce.code_version],
          ["Dataset fingerprint", <code key="f" className="text-xs">{String(d.reproduce.dataset_fingerprint).slice(0, 20)}</code>], ["Reproduce", <code key="c" className="text-xs">{d.reproduce.command}</code>]]} />
        <details className="mt-2"><summary className="cursor-pointer text-sm">Dataset quality per instrument</summary>
          <Table head={["Symbol", "Bars", "First", "Last", "Events", "Splits", "Dividends"]} rows={Object.entries(d.dataset_manifest?.instruments || {}).map(([s, v]: any) => [s, v.bars, v.first, v.last, v.events, v.splits, v.dividends])} /></details>
      </div>
      <Toast message={toast} onClose={() => setToast(null)} tone="info" />
    </Card>
  );
}
