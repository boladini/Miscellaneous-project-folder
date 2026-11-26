import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, Empty, ErrorState, Loading, StateBadge, Tabs } from "../components/ui";
import { dt, pct, signClass, usd } from "../format";
import { useApi } from "../hooks";
import { Lesson } from "./Experiment";
import { ClosedTable } from "./Portfolios";

export default function Journal() {
  const [q, setQ] = useState("");
  const [tab, setTab] = useState("theses");
  const { data, error, loading, reload } = useApi<any>(`/journal${q ? `?q=${encodeURIComponent(q)}` : ""}`);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-xl font-bold">Journal</h1><p className="text-sm text-slate-500">Hypotheses, outcomes and lessons. A profitable trade is not the same as a validated hypothesis.</p></div>
        <label className="text-xs">Search<input value={q} onChange={(e) => setQ(e.target.value)} className="mt-0.5 block w-56 rounded-md border border-slate-300 px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-800" /></label>
      </div>
      <Tabs label="Journal views" value={tab} onChange={setTab} tabs={[{ id: "theses", label: "Thesis outcomes" }, { id: "lessons", label: "Lessons" }, { id: "closed", label: "Closed trade reviews" }, { id: "timeline", label: "Timeline" }]} />
      {loading && !data ? <Loading /> : error ? <ErrorState error={error} onRetry={reload} /> : (
        <>
          {tab === "theses" && (data.theses.length ? <div className="grid gap-4 md:grid-cols-2">{data.theses.map((t: any) => (
            <Card key={t.experiment.id} title={<Link className="text-blue-700 underline dark:text-blue-400" to={`/experiments/${t.experiment.id}`}>{t.experiment.name}</Link>} actions={<StateBadge state={t.experiment.thesis_status} />}>
              <p className="text-sm">{t.hypothesis?.text || t.thesis}</p>
              {t.hypothesis?.prediction && <p className="mt-1 text-xs text-slate-500">Predicted: {t.hypothesis.prediction.metric} {t.hypothesis.prediction.direction} {t.hypothesis.prediction.threshold} within {t.hypothesis.prediction.horizon_sessions} sessions</p>}
              <p className="mt-2 text-sm">Realized: <span className={signClass(t.experiment.total_pnl)}>{usd(t.experiment.total_pnl)}</span> ({pct(t.experiment.return_on_budget)} of budget) over {t.experiment.closed_trades} closed trades
                {t.experiment.insufficient_history && <Badge tone="warn">too few trades to conclude</Badge>}</p>
              {t.latest_review && <p className="mt-1 text-xs text-slate-500">Latest monitor ({t.latest_review.session}): {t.latest_review.summary}</p>}
            </Card>))}</div> : <Empty title="No experiments yet" />)}
          {tab === "lessons" && <Card>{data.lessons.length ? data.lessons.map((l: any) => <Lesson key={l.id} l={l} />) : <Empty title="No lessons yet">The learning agent reviews experiments monthly.</Empty>}</Card>}
          {tab === "closed" && <Card><ClosedTable lots={data.closed_trades} /></Card>}
          {tab === "timeline" && <Card>{data.timeline.length ? <ol className="space-y-2 text-sm">{data.timeline.map((a: any, i: number) => (
            <li key={i} className="flex gap-3 border-b border-slate-100 pb-2 dark:border-slate-800"><time className="w-44 shrink-0 text-xs text-slate-500">{dt(a.at)}</time>
              <div><span className="font-medium">{a.action}</span> <span className="text-xs text-slate-500">by {a.actor} · {a.entity_type}</span></div></li>))}</ol> : <Empty title="Nothing recorded" />}</Card>}
        </>
      )}
    </div>
  );
}
