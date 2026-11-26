import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { api, newKey } from "../api";
import { EquityChart, ScoreBars } from "../components/charts";
import { Badge, Button, Card, Empty, ErrorState, Kv, Loading, Modal, StateBadge, Table, Tabs, Toast, SyntheticNote } from "../components/ui";
import { day, dt, familyLabel, num, pct, usd } from "../format";
import { useApi } from "../hooks";

export default function Approvals() {
  const [tab, setTab] = useState("pending");
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold">Approvals</h1>
        <p className="text-sm text-slate-500">AI-researched strategy proposals, ranked by score. Your only required choice is Yes or No.</p>
      </div>
      <Tabs label="Proposal views" value={tab} onChange={setTab} tabs={[{ id: "pending", label: "Pending" }, { id: "history", label: "History" }]} />
      {tab === "pending" ? <Pending /> : <History />}
    </div>
  );
}

function Pending() {
  const { data, error, loading, reload } = useApi<any>("/proposals?status=pending", { poll: 60000 });
  const [toast, setToast] = useState<{ m: string; t: "good" | "neutral" | "bad" } | null>(null);
  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorState error={error} onRetry={reload} />;
  const items = data?.items || [];
  return (
    <>
      {items.length === 0 ? (
        <Empty title="No pending proposals">The research agent publishes up to three qualified proposals per weekly run. When nothing meets the evidence, validation and score thresholds, it publishes none — by design.</Empty>
      ) : (
        <div className="space-y-4">{items.map((p: any) => <ProposalCard key={p.id} p={p} onDecided={(m, t) => { setToast({ m, t }); reload(); }} />)}</div>
      )}
      <Toast message={toast?.m || null} tone={toast?.t} onClose={() => setToast(null)} />
    </>
  );
}

function ProposalCard({ p, onDecided }: { p: any; onDecided: (msg: string, tone: "good" | "neutral" | "bad") => void }) {
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const c = p.card || {};
  const kind = p.kind !== "new" ? p.kind : null;

  async function decide(decision: "yes" | "no") {
    setBusy(true);
    setErr(null);
    try {
      const r = await api(`/proposals/${p.id}/decision`, { method: "POST", body: { decision, idempotency_key: newKey(), expected_scope_hash: p.scope_hash } });
      setConfirm(false);
      onDecided(r.message, decision === "yes" ? "good" : "neutral");
    } catch (e: any) {
      setErr(e.message);
      if (["expired", "blocked", "scope_mismatch", "not_pending"].includes(e.code)) setTimeout(() => onDecided(e.message, "bad"), 1500);
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900" aria-labelledby={`p-${p.id}`}>
      <div className="p-4 sm:p-5">
        <div className="flex flex-wrap items-center gap-2">
          {p.rank && <Badge tone="accent">#{p.rank}</Badge>}
          <Badge tone="info" icon={false}>{familyLabel[c.family] || c.family}</Badge>
          {kind && <Badge tone="warn">{kind} proposal</Badge>}
          {p.exploratory && <Badge tone="warn" title="Did not pass all robustness checks; allocation halved">exploratory</Badge>}
          {c.synthetic && <SyntheticNote />}
          {c.revisit_note && <Badge tone="info">revisited: new evidence</Badge>}
          <span className="ml-auto text-xs text-slate-500">expires {dt(p.expires_at)}</span>
        </div>
        <h2 id={`p-${p.id}`} className="mt-2 text-lg font-bold">{c.name}</h2>
        <p className="mt-1 text-sm text-slate-700 dark:text-slate-300">{c.thesis}</p>
        <div className="mt-4 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <Field label="Max allocation" value={usd(Number(c.max_allocation ?? p.budget), 0)} />
          <Field label="Horizon" value={c.horizon || "—"} />
          <Field label="Score" value={p.score != null ? `${p.score}/100` : "n/a"} hint="Ranking heuristic, not a probability of success" />
          <Field label="Evidence quality" value={c.evidence_quality != null ? `${c.evidence_quality}/100 · ${c.evidence_count ?? 0} source(s)` : "—"} />
        </div>
        <div className="mt-4 grid gap-3 text-sm md:grid-cols-2">
          <Block title="Why now">{c.why_now || "—"}</Block>
          <Block title="Expected trading cadence">{c.cadence || "—"}</Block>
          <Block title="Entry (summary)"><code className="break-words text-xs">{c.entry_summary || "—"}</code></Block>
          <Block title="Exit (summary)"><code className="break-words text-xs">{c.exit_summary || "—"}</code></Block>
          <Block title="Key counterargument" tone="warn">{c.key_counterargument || "—"}</Block>
          <Block title="Downside scenario" tone="bad">{c.downside_scenario || "—"}</Block>
        </div>
        {c.backtest_status && (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-600 dark:text-slate-400">
            <Badge tone={p.exploratory ? "warn" : "good"}>{c.backtest_status}</Badge>
            {c.backtest_headline && <span>Backtest: return {pct(c.backtest_headline.total_return)} · vs benchmark {pct(c.backtest_headline.excess_return)} · max DD {pct(c.backtest_headline.max_drawdown)} · {c.backtest_headline.trade_count ?? 0} trades</span>}
            {c.data_freshness && <span>· data through {day(c.data_freshness)}</span>}
          </div>
        )}
        {c.diff && <Block title="Proposed change (version diff)"><pre className="overflow-x-auto text-xs">{JSON.stringify(c.diff, null, 1)}</pre></Block>}
        {c.legacy_treatment && <p className="mt-2 text-xs text-slate-600">{c.legacy_treatment}</p>}
        {err && <p role="alert" className="mt-3 text-sm text-rose-700 dark:text-rose-400">✕ {err}</p>}
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <Button variant="yes" onClick={() => setConfirm(true)} disabled={busy}>Yes</Button>
          <Button variant="no" onClick={() => decide("no")} disabled={busy}>No</Button>
          <button className="ml-auto text-sm font-medium text-blue-700 underline-offset-2 hover:underline dark:text-blue-400" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? "Hide details" : "Evidence, rules & backtest"}
          </button>
        </div>
      </div>
      {open && <Details id={p.id} />}
      <Modal open={confirm} title="Approve and activate?" onClose={() => setConfirm(false)}>
        <div className="space-y-2 text-sm text-slate-700 dark:text-slate-300">
          <p>Approving <strong>{c.name}</strong> authorizes the system to place <strong>automatic hypothetical (paper) trades</strong> within these limits, without asking you for each order:</p>
          <ul className="list-disc pl-5">
            <li>Maximum allocation {usd(Number(c.max_allocation ?? p.budget), 0)} in the {familyLabel[c.family]} portfolio</li>
            <li>Only the exact versioned rules shown (entries, exits, holding limits, risk limits)</li>
            <li>Authorization period about one year; then exits only</li>
          </ul>
          <p>No real money is involved. Nothing is bought immediately: the next eligible signal and market open come first. Rule changes always require a new approval.</p>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <Button onClick={() => setConfirm(false)}>Cancel</Button>
          <Button variant="yes" disabled={busy} onClick={() => decide("yes")}>{busy ? "Approving…" : "Yes, approve"}</Button>
        </div>
      </Modal>
    </article>
  );
}

function Field({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg bg-slate-50 p-2.5 dark:bg-slate-800/60" title={hint}>
      <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-0.5 font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function Block({ title, children, tone }: { title: string; children: React.ReactNode; tone?: "warn" | "bad" }) {
  const border = tone === "warn" ? "border-l-amber-500" : tone === "bad" ? "border-l-rose-500" : "border-l-slate-300 dark:border-l-slate-600";
  return (
    <div className={`border-l-4 ${border} pl-3`}>
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</div>
      <div className="mt-0.5 min-w-0 break-words">{children}</div>
    </div>
  );
}

export function Details({ id }: { id: string }) {
  const { data, error, loading, reload } = useApi<any>(`/proposals/${id}`);
  const [tab, setTab] = useState("evidence");
  if (loading && !data) return <Loading />;
  if (error) return <div className="p-4"><ErrorState error={error} onRetry={reload} /></div>;
  const d = data!;
  return (
    <div className="border-t border-slate-200 p-4 dark:border-slate-800 sm:p-5">
      <Tabs label="Proposal details" value={tab} onChange={setTab} tabs={[
        { id: "evidence", label: `Evidence (${d.evidence.length})` }, { id: "backtest", label: "Backtest" }, { id: "rules", label: "Exact rules" },
        { id: "risk", label: "Risk & scope" }, { id: "critique", label: "Critique" }, { id: "score", label: "Score" }, { id: "qa", label: "Ask" },
      ]} />
      <div className="pt-4">
        {tab === "evidence" && <EvidenceList items={d.evidence} />}
        {tab === "backtest" && <BacktestPanel bt={d.backtest} />}
        {tab === "rules" && (
          <div className="space-y-3 text-sm">
            <Kv items={[["Strategy version", `v${d.version}`], ["Rule hash", <code key="h" className="break-all text-xs">{d.rule_hash.slice(0, 16)}…</code>],
              ["Entry", <code key="e" className="text-xs">{d.rules_text.entry}</code>], ["Exit", <code key="x" className="text-xs">{d.rules_text.exit}</code>]]} />
            <details><summary className="cursor-pointer text-sm font-medium">Machine-readable rule JSON</summary>
              <pre className="mt-2 max-h-96 overflow-auto rounded-lg bg-slate-50 p-3 text-xs dark:bg-slate-800">{JSON.stringify(d.rules, null, 2)}</pre></details>
          </div>
        )}
        {tab === "risk" && (
          <div className="space-y-3">
            <Kv items={[["Budget", usd(Number(d.scope.budget))], ["Universe", `${d.scope.universe.asset_types.join(", ")} · min price $${d.scope.universe.min_price} · min 20d $ volume ${num(d.scope.universe.min_avg_dollar_volume_20d, 0)}`],
              ["Max positions", d.scope.allocation.max_positions], ["Max per position", pct(d.scope.allocation.max_position_fraction, 1, false) + " of NAV"],
              ["Max experiment", pct(d.scope.allocation.max_experiment_fraction, 0, false) + " of NAV"], ["Max holding", `${d.scope.max_holding_sessions} sessions`],
              ["Risk policy version", d.scope.risk_policy_version], ["Drawdown circuit breaker", pct(d.scope.risk_limits.drawdown_circuit_breaker, 0, false)],
              ["Min uncommitted cash", pct(d.scope.risk_limits.min_cash_fraction, 0, false)], ["Paper trading only", "Yes"]]} />
            <p className="text-xs text-slate-500">Scope hash <code>{d.scope_hash.slice(0, 16)}…</code> — approval authorizes exactly this scope.</p>
          </div>
        )}
        {tab === "critique" && d.critique && (
          <div className="space-y-2">
            <p className="text-xs text-slate-500">{d.critique.note} Provider: {d.critique.provider}.</p>
            {d.critique.summary && <p className="text-sm">{d.critique.summary}</p>}
            <ul className="space-y-2">{d.critique.items.map((it: any, i: number) => (
              <li key={i} className="rounded-lg border border-slate-200 p-3 text-sm dark:border-slate-800">
                <div className="flex flex-wrap gap-2"><Badge tone={it.severity === "critical" || it.severity === "high" ? "bad" : it.severity === "medium" ? "warn" : "neutral"}>{it.severity}</Badge><Badge icon={false}>{it.category}</Badge></div>
                <div className="mt-1">{it.issue}</div><div className="mt-1 text-xs text-slate-500">Mitigation: {it.mitigation}</div>
              </li>))}</ul>
          </div>
        )}
        {tab === "score" && (
          <div className="grid gap-4 lg:grid-cols-2">
            <div><ScoreBars components={d.components} /><p className="mt-2 text-xs text-slate-500">Weights: evidence 25%, risk-adjusted potential 20%, robustness 20%, applicability 15%, downside controls 10%, diversification 10%. {d.uncertainty?.note}</p></div>
            <div className="space-y-2 text-sm">
              <div className="font-semibold">Hard gates</div>
              <ul className="flex flex-wrap gap-2">{Object.entries(d.gates || {}).map(([k, v]) => <li key={k}><Badge tone={v ? "good" : "warn"}>{k.replace(/_/g, " ")}</Badge></li>)}</ul>
              <div className="font-semibold">Rationale</div>
              <ul className="space-y-1 text-xs text-slate-600 dark:text-slate-400">{Object.entries(d.rationale || {}).map(([k, v]) => <li key={k}><strong>{k.replace(/_/g, " ")}:</strong> {String(v)}</li>)}</ul>
            </div>
          </div>
        )}
        {tab === "qa" && <QA id={id} initial={d.qa} />}
      </div>
    </div>
  );
}

export function EvidenceList({ items }: { items: any[] }) {
  if (!items?.length) return <Empty title="No evidence records linked" />;
  return (
    <ul className="space-y-3">
      {items.map((e) => (
        <li key={e.id} className="rounded-lg border border-slate-200 p-3 text-sm dark:border-slate-800">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={e.stance === "supporting" ? "good" : e.stance === "disconfirming" ? "bad" : "neutral"}>{e.stance}</Badge>
            <Badge icon={false}>{e.source.source_type.replace("_", " ")}</Badge>
            <Badge tone={e.quality_label === "high" ? "good" : e.quality_label === "medium" ? "info" : "warn"}>quality {e.quality_label}</Badge>
            {e.source.synthetic && <SyntheticNote />}
            {!e.claim_supported && <Badge tone="warn">claim not verified against text</Badge>}
            {e.is_new && <Badge tone="info" icon={false}>new</Badge>}
          </div>
          <div className="mt-2 font-medium">{e.source.url && !e.source.synthetic ? <a className="text-blue-700 underline dark:text-blue-400" href={e.source.url} target="_blank" rel="noopener noreferrer nofollow">{e.source.title}</a> : e.source.title}</div>
          <div className="text-xs text-slate-500">{[e.source.authors, e.source.publisher, e.source.published_at && day(e.source.published_at)].filter(Boolean).join(" · ")}{e.source.cited_by_count != null && ` · cited by ${e.source.cited_by_count}`}</div>
          <p className="mt-2">{e.claim}</p>
          {e.excerpt && <blockquote className="mt-2 border-l-2 border-slate-300 pl-3 text-xs italic text-slate-600 dark:border-slate-600 dark:text-slate-400">“{e.excerpt}”</blockquote>}
          <p className="mt-2 text-xs text-slate-500"><strong>Limitations:</strong> {e.limitations}</p>
          <p className="mt-1 text-[11px] text-slate-400">Provenance: {e.source.provider} · {e.source.retrieval_id} · retrieved {dt(e.source.retrieved_at)} · {e.extraction_method}. {e.source.verification_note}</p>
        </li>
      ))}
    </ul>
  );
}

export function BacktestPanel({ bt }: { bt: any }) {
  if (!bt) return <Empty title="No backtest linked" />;
  if (bt.status !== "completed") return <Empty title={`Backtest: ${bt.status.replace("_", " ")}`}>{bt.reason}</Empty>;
  const m = bt.metrics?.metrics || {};
  const rob = bt.metrics?.robustness || {};
  const eq = (bt.series?.equity || []).map((r: any) => ({ session: r[0], nav: r[1], benchmark_nav: r[2] }));
  const segs = ["train", "validation", "holdout", "full", "stress_cost"].filter((k) => m[k]);
  return (
    <div className="space-y-4">
      <EquityChart data={eq} aName="Strategy (allocated capital)" bName="Benchmark (same capital)" emptyText="No equity series" />
      <Table caption="Backtest metrics by partition" head={["Segment", "Return", "vs bench", "Sharpe", "Max DD", "Trades", "Win rate"]}
        rows={segs.map((k) => [k === "stress_cost" ? "Stress costs (full)" : k, pct(m[k].total_return), pct(m[k].excess_return), num(m[k].sharpe),
          pct(m[k].max_drawdown), m[k].trade_count ?? "—", pct(m[k].win_rate, 0, false)])} />
      <div className="flex flex-wrap gap-2">{Object.entries(rob).map(([k, v]) => <Badge key={k} tone={v ? "good" : "warn"}>{k.replace(/_/g, " ")}</Badge>)}</div>
      {m.sensitivity_entry_threshold_x && <p className="text-xs text-slate-600 dark:text-slate-400">Parameter sensitivity (entry thresholds ×0.8 / ×1.2): {Object.entries(m.sensitivity_entry_threshold_x).map(([k, v]: any) => `×${k}: ${pct(v)}`).join(" · ")}</p>}
      <div className="text-xs text-slate-600 dark:text-slate-400">
        <div className="font-semibold">Assumptions</div>
        <ul className="list-disc pl-5">{Object.entries(bt.parameters || {}).map(([k, v]) => <li key={k}>{k}: {String(v)}</li>)}</ul>
        <div className="mt-2 font-semibold">Limitations</div>
        <ul className="list-disc pl-5">{(bt.limitations || []).map((l: string, i: number) => <li key={i}>{l}</li>)}</ul>
        <p className="mt-2">Data: {bt.dataset_manifest?.provider} ({bt.dataset_manifest?.adjustment_basis}) · {bt.instrument_count || Object.keys(bt.dataset_manifest?.instruments || {}).length} instruments · fingerprint <code>{String(bt.dataset_manifest?.fingerprint || "").slice(0, 12)}</code> · {bt.code_version}</p>
      </div>
    </div>
  );
}

function QA({ id, initial }: { id: string; initial: any[] }) {
  const [msgs, setMsgs] = useState<any[]>(initial || []);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function ask(e: FormEvent) {
    e.preventDefault();
    if (q.trim().length < 3) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await api(`/proposals/${id}/qa`, { method: "POST", body: { question: q } });
      setMsgs([...msgs, r]);
      setQ("");
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }
  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-500">Optional. Answers explain the proposal from stored evidence and cannot change its rules or scope.</p>
      {msgs.map((m, i) => (
        <div key={i} className="rounded-lg bg-slate-50 p-3 text-sm dark:bg-slate-800/60">
          <div className="font-semibold">Q: {m.question}</div><div className="mt-1 whitespace-pre-wrap">{m.answer}</div>
          {m.citations?.length > 0 && <div className="mt-1 text-xs text-slate-500">Cites evidence: {m.citations.map((c: string) => c.slice(0, 8)).join(", ")}</div>}
          <div className="text-[11px] text-slate-400">{m.provider}</div>
        </div>
      ))}
      <form onSubmit={ask} className="flex gap-2">
        <label className="sr-only" htmlFor={`q-${id}`}>Question</label>
        <input id={`q-${id}`} value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. What is the main risk?" maxLength={1000}
          className="min-w-0 flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800" />
        <Button type="submit" disabled={busy}>{busy ? "…" : "Ask"}</Button>
      </form>
      {err && <p role="alert" className="text-sm text-rose-700">✕ {err}</p>}
    </div>
  );
}

function History() {
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const { data, error, loading, reload } = useApi<any>(`/proposals?limit=200${status ? `&status=${status}` : ""}${q ? `&q=${encodeURIComponent(q)}` : ""}`);
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Card title="Decision history" actions={
      <>
        <label className="sr-only" htmlFor="hist-status">Status</label>
        <select id="hist-status" value={status} onChange={(e) => setStatus(e.target.value)} className="rounded-md border border-slate-300 px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-800">
          <option value="">All</option>{["approved", "rejected", "expired", "blocked", "withdrawn", "pending"].map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <label className="sr-only" htmlFor="hist-q">Search</label>
        <input id="hist-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search…" className="w-40 rounded-md border border-slate-300 px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-800" />
      </>}>
      {loading && !data ? <Loading /> : error ? <ErrorState error={error} onRetry={reload} /> : (
        <>
          <Table caption="Proposal history" head={["Proposal", "Kind", "State", "Score", "Created", "Decided", ""]} rows={(data?.items || []).map((p: any) => [
            <span key="n" className="font-medium">{p.card?.name}</span>, p.kind, <StateBadge key="s" state={p.state} />, p.score ?? "—", day(p.created_at), p.decided_at ? day(p.decided_at) : "—",
            <span key="a" className="flex gap-2">{p.experiment_id && <Link className="text-blue-700 underline dark:text-blue-400" to={`/experiments/${p.experiment_id}`}>Experiment</Link>}
              <button className="text-blue-700 underline dark:text-blue-400" onClick={() => setOpen(open === p.id ? null : p.id)}>{open === p.id ? "Hide" : "Details"}</button></span>,
          ])} empty="No proposals yet." />
          {open && <div className="mt-4 rounded-xl border border-slate-200 dark:border-slate-800"><Details id={open} /></div>}
        </>
      )}
    </Card>
  );
}
