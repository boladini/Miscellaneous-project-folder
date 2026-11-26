import { useState } from "react";
import { api } from "../api";
import { Badge, Button, Card, Empty, ErrorState, Loading, StateBadge, Table, Tabs, Toast } from "../components/ui";
import { ago, day, dt } from "../format";
import { useApi, useJobWatcher } from "../hooks";
import { EvidenceList } from "./Approvals";

export default function Research() {
  const [tab, setTab] = useState("briefing");
  const runs = useApi<any>("/research/runs", { poll: 60000 });
  const [toast, setToast] = useState<string | null>(null);
  const { job, watch } = useJobWatcher(() => { runs.reload(); setToast("Research run finished. See the briefing."); });
  async function runNow() {
    try {
      const r = await api("/jobs/research", { method: "POST" });
      watch(r.job_id, r.state);
      setToast(r.created ? "Research queued. The worker runs it in the background — you can close this page." : r.note);
    } catch (e: any) { setToast(e.message); }
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-xl font-bold">Research</h1><p className="text-sm text-slate-500">Weekly autonomous research: sources, evidence, disconfirming findings and rejected ideas.</p></div>
        <div className="flex items-center gap-2">
          {job && <span className="text-xs"><StateBadge state={job.state} /> {job.progress?.step}</span>}
          <Button variant="primary" onClick={runNow}>Run research now</Button>
        </div>
      </div>
      <Tabs label="Research views" value={tab} onChange={setTab} tabs={[{ id: "briefing", label: "Weekly briefing" }, { id: "library", label: "Source library" }, { id: "rejected", label: "Rejected ideas" }, { id: "runs", label: "Run history" }]} />
      {tab === "briefing" && (runs.loading && !runs.data ? <Loading /> : runs.data?.items?.length ? <Briefing id={runs.data.items[0].id} /> : <Empty title="No research runs yet">The first run happens Monday 7:00 AM Central, or use “Run research now”.</Empty>)}
      {tab === "library" && <Library />}
      {tab === "rejected" && <Rejected />}
      {tab === "runs" && <Card><Table head={["Started", "State", "Mode", "AI", "Headline", "Stats", "Cost"]} rows={(runs.data?.items || []).map((r: any) => [
        dt(r.started_at), <StateBadge key="s" state={r.state} />, r.mode, r.llm_provider + (r.llm_model ? ` · ${r.llm_model}` : ""), r.headline,
        <span key="t" className="text-xs">{r.stats?.sources_new ?? 0} new sources · {r.stats?.evidence ?? 0} evidence · {r.stats?.candidates ?? 0} candidates · {r.stats?.published ?? 0} published</span>,
        `$${(r.cost_usd || 0).toFixed(2)}`])} empty="No runs." /></Card>}
      <Toast message={toast} onClose={() => setToast(null)} tone="info" />
    </div>
  );
}

function Briefing({ id }: { id: string }) {
  const { data, error, loading, reload } = useApi<any>(`/research/runs/${id}`);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  const r = data!;
  const b = r.briefing || {};
  return (
    <div className="space-y-4">
      <Card title={`Briefing · ${dt(r.started_at)}`} actions={<Badge tone={r.mode === "demo" ? "warn" : "good"}>{r.mode}</Badge>}>
        <p className="text-base font-semibold">{b.headline}</p>
        <p className="mt-1 text-xs text-slate-500">AI: {r.llm_provider}{r.llm_model ? ` (${r.llm_model})` : " — deterministic keyword extraction and rule templates"} · prompt {r.prompt_version} · cost ${(r.cost_usd || 0).toFixed(2)}</p>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {Object.entries(b.topics || {}).map(([t, v]: any) => (
            <div key={t} className="rounded-lg bg-slate-50 p-3 text-sm dark:bg-slate-800/60">
              <div className="font-semibold capitalize">{t}</div>
              <div className="text-xs text-slate-600 dark:text-slate-400">{v.supporting} supporting · {v.disconfirming} disconfirming · {v.background} background</div>
            </div>))}
        </div>
        {b.notes && Object.keys(b.notes).length > 0 && <ul className="mt-3 list-disc pl-5 text-xs text-slate-600 dark:text-slate-400">{Object.entries(b.notes).map(([k, v]: any) => <li key={k}><strong>{k}:</strong> {v}</li>)}</ul>}
      </Card>
      <Card title="Source coverage">
        <Table head={["Provider", "Channel", "Status", "Detail"]} rows={Object.entries(r.coverage || {}).map(([k, v]: any) => [k, v.channel || "—", <StateBadge key="s" state={v.status} />,
          <span key="d" className="text-xs">{v.reason || v.note || (v.queries_ok !== undefined ? `${v.queries_ok} ok / ${v.queries_failed} failed queries` : "")}{v.errors?.length ? ` · ${v.errors[0]}` : ""}</span>])} />
      </Card>
      <Card title={`Candidates rejected this run (${(b.rejected || []).length})`}>
        <Table head={["Idea", "Reason", "Score"]} rows={(b.rejected || []).map((x: any) => [x.name, <span key="r" className="text-xs">{x.reason}</span>, x.score ?? "—"])} empty="None rejected." />
      </Card>
      <Card title={`Evidence gathered (${r.evidence.length})`}><EvidenceList items={r.evidence} /></Card>
    </div>
  );
}

function Library() {
  const [f, setF] = useState({ q: "", topic: "", stance: "", quality: "", source_type: "", start: "", end: "" });
  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v) as [string, string][]);
  const { data, error, loading, reload } = useApi<any>(`/evidence?limit=100&${qs}`);
  const sel = (k: keyof typeof f, opts: string[], label: string) => (
    <label className="text-xs">{label}
      <select value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} className="mt-0.5 block w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-800">
        <option value="">Any</option>{opts.map((o) => <option key={o} value={o}>{o.replace("_", " ")}</option>)}
      </select></label>);
  return (
    <Card title={`Source library ${data ? `(${data.total})` : ""}`}>
      <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
        <label className="col-span-2 text-xs">Search<input value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} placeholder="claim, title, abstract…" className="mt-0.5 block w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-800" /></label>
        {sel("topic", ["reversal", "momentum", "pead", "asymmetric", "lowvol"], "Topic")}
        {sel("stance", ["supporting", "disconfirming", "background"], "Stance")}
        {sel("quality", ["high", "medium", "low"], "Evidence quality")}
        {sel("source_type", ["peer_reviewed", "preprint", "filing", "news", "social", "synthetic"], "Source type")}
        <label className="text-xs">From<input type="date" value={f.start} onChange={(e) => setF({ ...f, start: e.target.value })} className="mt-0.5 block w-full rounded-md border border-slate-300 px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-800" /></label>
      </div>
      {loading && !data ? <Loading /> : error ? <ErrorState error={error} onRetry={reload} /> : <EvidenceList items={data?.items || []} />}
    </Card>
  );
}

function Rejected() {
  const { data, error, loading, reload } = useApi<any>("/research/rejected");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  return (
    <Card title="Rejected & expired ideas (searchable history)">
      <p className="mb-3 text-xs text-slate-500">Rejected ideas are revisited only if materially new evidence appears; the reason is recorded on the new proposal.</p>
      <Table head={["Idea", "Reason", "Score", "When"]} rows={(data?.items || []).map((x: any) => [x.name, <span key="r" className="text-xs">{x.reason}</span>, x.score ?? "—", x.at ? `${day(x.at)} (${ago(x.at)})` : "—"])} empty="Nothing rejected yet." />
    </Card>
  );
}
