import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { Badge, Button, Card, Empty, ErrorState, Kv, Loading, Stat, StateBadge, Table, Tabs, Toast, TypedConfirm } from "../components/ui";
import { day, dt, pct, signClass, usd } from "../format";
import { useApi } from "../hooks";
import { BacktestPanel, EvidenceList } from "./Approvals";
import { ClosedTable, TradesTable } from "./Portfolios";

export default function ExperimentDetail() {
  const { id } = useParams();
  const { data, error, loading, reload } = useApi<any>(`/experiments/${id}`, { poll: 60000 });
  const [tab, setTab] = useState("live");
  const [closing, setClosing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState error={error} onRetry={reload} />;
  const d = data!;
  const e = d.experiment;
  async function close() {
    setBusy(true);
    try {
      const r = await api(`/experiments/${id}/close`, { method: "POST", body: { confirm: "CLOSE" } });
      setToast(r.message);
      setClosing(false);
      reload();
    } catch (err: any) { setToast(err.message); } finally { setBusy(false); }
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link to={`/portfolios/${e.portfolio_id}`} className="text-xs text-blue-700 underline dark:text-blue-400">← Portfolio</Link>
          <h1 className="text-xl font-bold">{e.name}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2"><StateBadge state={e.status} /><span className="text-xs text-slate-500">thesis</span><StateBadge state={e.thesis_status} />
            <span className="text-xs text-slate-500">approved {dt(e.approved_at)} · authorized until {day(e.ends_by)} · v{d.version.version}</span></div>
          {e.status_reason && <p className="mt-1 text-xs text-slate-500">{e.status_reason}</p>}
        </div>
        {!["closed", "completed", "closing"].includes(e.status) && <Button variant="danger" onClick={() => setClosing(true)}>Close experiment…</Button>}
      </div>
      <Card>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="Budget" value={usd(e.budget, 0)} />
          <Stat label="Total P&L" value={usd(e.total_pnl)} valueClass={signClass(e.total_pnl)} />
          <Stat label="Return on budget" value={pct(e.return_on_budget)} valueClass={signClass(e.return_on_budget)} />
          <Stat label="Closed trades" value={e.closed_trades} sub={e.insufficient_history ? "too few to judge" : undefined} />
          <Stat label="Win rate" value={pct(e.win_rate, 0, false)} />
          <Stat label="Open positions" value={e.open_positions} />
        </div>
        {e.closed_trades === 0 && e.open_positions === 0 && e.status === "active" && (
          <p className="mt-3 text-sm text-slate-600 dark:text-slate-300">Approved — awaiting eligible market data/signal. Entry rules are evaluated after each finalized close; fills happen at the next session open.</p>)}
      </Card>
      <Tabs label="Experiment details" value={tab} onChange={setTab} tabs={[
        { id: "live", label: "Live paper performance" }, { id: "thesis", label: "Thesis & rules" }, { id: "backtest", label: "Historical test" },
        { id: "timeline", label: "Timeline" }, { id: "evidence", label: "Evidence" }, { id: "reviews", label: "Reviews & lessons" }, { id: "revisions", label: `Revisions (${d.revisions.length})` }]} />
      {tab === "live" && (
        <div className="space-y-4">
          <Card title="Open positions"><Table head={["Symbol", "Qty", "Entry", "Last", "Unrealized", "Opened"]} rows={d.positions.map((x: any) => [x.symbol, x.quantity, usd(x.entry_price), usd(x.last_price),
            <span key="u" className={signClass(x.unrealized_pnl)}>{usd(x.unrealized_pnl)}</span>, day(x.opened_session)])} empty="No open positions." /></Card>
          <Card title="Orders & fills"><TradesTable trades={d.trades} /></Card>
          <Card title="Closed trades"><ClosedTable lots={d.closed_lots} /></Card>
        </div>
      )}
      {tab === "thesis" && (
        <Card>
          <p className="text-sm">{d.version.thesis}</p>
          <div className="mt-3"><Kv items={[["Hypothesis", d.version.hypothesis?.text || "—"],
            ["Pre-registered prediction", d.version.hypothesis?.prediction ? `${d.version.hypothesis.prediction.metric} ${d.version.hypothesis.prediction.direction} ${d.version.hypothesis.prediction.threshold} within ${d.version.hypothesis.prediction.horizon_sessions} sessions` : "—"],
            ["Entry", <code key="e" className="text-xs">{d.version.rules_text.entry}</code>], ["Exit", <code key="x" className="text-xs">{d.version.rules_text.exit}</code>],
            ["Rule hash", <code key="h" className="text-xs">{d.version.rule_hash.slice(0, 16)}…</code>], ["Generated by", d.version.generated_by]]} /></div>
          <details className="mt-3"><summary className="cursor-pointer text-sm font-medium">Exact versioned rules (JSON)</summary>
            <pre className="mt-2 max-h-96 overflow-auto rounded-lg bg-slate-50 p-3 text-xs dark:bg-slate-800">{JSON.stringify(d.version.rules, null, 2)}</pre></details>
        </Card>
      )}
      {tab === "backtest" && <Card><BacktestPanel bt={d.backtest} /></Card>}
      {tab === "timeline" && <Card>{d.timeline.length ? <ol className="space-y-2 text-sm">{d.timeline.map((t: any, i: number) => (
        <li key={i} className="flex gap-3 border-b border-slate-100 pb-2 dark:border-slate-800"><time className="w-40 shrink-0 text-xs text-slate-500">{dt(t.at)}</time>
          <div><Badge icon={false}>{t.type}</Badge>{t.session && <span className="ml-2 text-xs">session {t.session}</span>}{t.result !== undefined && <span className="ml-2"><Badge tone={t.result ? "good" : "neutral"}>{t.result ? "rules met / allowed" : "not acted on"}</Badge></span>}
            {t.detail?.risk?.reasons?.length > 0 && <div className="text-xs text-slate-500">Risk: {t.detail.risk.reasons.join("; ")}</div>}</div></li>))}</ol> : <Empty title="No events yet" />}</Card>}
      {tab === "evidence" && <Card><EvidenceList items={d.evidence} /></Card>}
      {tab === "reviews" && (
        <div className="space-y-4">
          <Card title="Monthly lessons">{d.lessons.length ? d.lessons.map((l: any) => <Lesson key={l.id} l={l} />) : <Empty title="No monthly review yet">Reviews run on the first trading Monday of each month.</Empty>}</Card>
          <Card title="Daily thesis monitoring"><Table head={["Session", "Status", "Summary", "Confidence"]} rows={d.reviews.map((r: any) => [day(r.session), <StateBadge key="s" state={r.status} />, <span key="x" className="text-xs">{r.summary}</span>, r.confidence])} empty="No daily reviews yet." /></Card>
        </div>
      )}
      {tab === "revisions" && <Card>{d.revisions.length ? <Table head={["Proposal", "Kind", "State", "Created"]} rows={d.revisions.map((r: any) => [r.card?.name, r.kind, <StateBadge key="s" state={r.state} />, day(r.created_at)])} /> :
        <Empty title="No proposed revisions">Revisions are proposed by the monthly learning review and always require your approval; existing holdings keep their original rules until exit.</Empty>}</Card>}
      <TypedConfirm open={closing} title="Close this experiment?" word="CLOSE" busy={busy} onClose={() => setClosing(false)} onConfirm={close}
        body={<>New entries stop immediately. All open positions are sold at the next eligible session open after the next daily cycle — never at a past price. This cannot be undone.</>} />
      <Toast message={toast} onClose={() => setToast(null)} />
    </div>
  );
}

export function Lesson({ l }: { l: any }) {
  return (
    <div className="mb-3 rounded-lg border border-slate-200 p-3 text-sm dark:border-slate-800">
      <div className="flex flex-wrap gap-2"><Badge icon={false}>{l.period}</Badge><StateBadge state={l.outcome} /><Badge tone="info">recommend: {l.recommendation}</Badge><Badge icon={false}>confidence {l.confidence}</Badge></div>
      <p className="mt-2">{l.lesson}</p>
      <p className="mt-1 text-xs text-slate-500"><strong>Competing explanation:</strong> {l.competing_explanation}</p>
      {l.proposal_id && <Link to="/approvals" className="mt-1 inline-block text-xs text-blue-700 underline dark:text-blue-400">A proposal awaits your decision →</Link>}
    </div>
  );
}
