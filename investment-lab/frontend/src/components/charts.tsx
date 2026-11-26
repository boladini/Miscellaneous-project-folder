import { useState } from "react";
import {
  Area, AreaChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, Legend, Bar, BarChart, Cell,
} from "recharts";
import { day, pct, usd } from "../format";
import { Empty } from "./ui";

// Palette slots (validated reference palette): 1 blue = strategy, 2 orange = benchmark.
const C = {
  s1: "var(--series-1)", s2: "var(--series-2)", s3: "var(--series-3)", s4: "var(--series-4)", s5: "var(--series-5)",
  grid: "var(--grid)", axis: "var(--axis)", neg: "var(--series-8)",
};
export const SERIES = [C.s1, C.s2, C.s3, C.s4, C.s5];

const axisProps = { tick: { fill: "var(--axis)", fontSize: 11 }, tickLine: false, axisLine: { stroke: "var(--grid)" } };

function TableToggle({ show, onToggle }: { show: boolean; onToggle: () => void }) {
  return (
    <button onClick={onToggle} className="text-xs font-medium text-blue-700 underline-offset-2 hover:underline dark:text-blue-400">
      {show ? "Show chart" : "Show as table"}
    </button>
  );
}

function TipBox({ active, payload, label, fmt }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs shadow-md dark:border-slate-700 dark:bg-slate-900">
      <div className="mb-1 font-semibold text-slate-700 dark:text-slate-200">{day(label)}</div>
      {payload.map((p: any) => (
        <div key={p.dataKey} className="flex items-center justify-between gap-4 text-slate-700 dark:text-slate-200">
          <span className="flex items-center gap-1.5"><span className="inline-block h-2 w-2 rounded-full" style={{ background: p.color }} />{p.name}</span>
          <span className="tabular-nums font-medium">{fmt ? fmt(p.value) : p.value}</span>
        </div>
      ))}
    </div>
  );
}

/** Strategy vs benchmark on one axis (same units: USD). Only stored snapshots are plotted. */
export function EquityChart({ data, xKey = "session", aKey = "nav", bKey = "benchmark_nav", aName = "Portfolio NAV", bName = "Benchmark (price-only)", height = 260, emptyText }: {
  data: any[]; xKey?: string; aKey?: string; bKey?: string; aName?: string; bName?: string; height?: number; emptyText?: string;
}) {
  const [table, setTable] = useState(false);
  if (!data?.length) return <Empty title={emptyText || "No valuation history yet"}>Charts show only stored daily snapshots; nothing is plotted until the first daily cycle runs.</Empty>;
  const hasB = data.some((d) => d[bKey] !== null && d[bKey] !== undefined);
  return (
    <div className="viz-root">
      <div className="mb-1 flex justify-end"><TableToggle show={table} onToggle={() => setTable(!table)} /></div>
      {table ? (
        <div className="max-h-72 overflow-auto text-xs">
          <table className="w-full"><thead><tr className="text-left text-slate-500"><th className="py-1">Session</th><th>{aName}</th>{hasB && <th>{bName}</th>}</tr></thead>
            <tbody>{data.slice().reverse().map((d) => <tr key={d[xKey]}><td className="py-0.5">{d[xKey]}</td><td className="tabular-nums">{usd(d[aKey])}</td>{hasB && <td className="tabular-nums">{usd(d[bKey])}</td>}</tr>)}</tbody></table>
        </div>
      ) : (
        <div style={{ height }} role="img" aria-label={`${aName}${hasB ? ` versus ${bName}` : ""} over time`}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={C.grid} strokeDasharray="0" vertical={false} />
              <XAxis dataKey={xKey} {...axisProps} minTickGap={40} tickFormatter={(v) => day(v).replace(/, \d{4}$/, "")} />
              <YAxis {...axisProps} width={64} domain={["auto", "auto"]} tickFormatter={(v) => usd(v, 0)} />
              <Tooltip content={<TipBox fmt={(v: number) => usd(v)} />} cursor={{ stroke: "var(--axis)", strokeWidth: 1 }} />
              {hasB && <Legend iconType="plainline" wrapperStyle={{ fontSize: 12, color: "var(--text-secondary)" }} />}
              <Line type="monotone" dataKey={aKey} name={aName} stroke={C.s1} strokeWidth={2} dot={false} activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface-1)" }} isAnimationActive={false} />
              {hasB && <Line type="monotone" dataKey={bKey} name={bName} stroke={C.s2} strokeWidth={2} dot={false} connectNulls activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface-1)" }} isAnimationActive={false} />}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

export function DrawdownChart({ data, height = 140 }: { data: any[]; height?: number }) {
  if (!data?.length) return null;
  return (
    <div className="viz-root" style={{ height }} role="img" aria-label="Drawdown from peak over time">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={C.grid} vertical={false} />
          <XAxis dataKey="session" {...axisProps} minTickGap={40} tickFormatter={(v) => day(v).replace(/, \d{4}$/, "")} />
          <YAxis {...axisProps} width={64} tickFormatter={(v) => pct(v, 0, false)} domain={["dataMin", 0]} />
          <Tooltip content={<TipBox fmt={(v: number) => pct(v)} />} />
          <Area type="monotone" dataKey="drawdown" name="Drawdown" stroke={C.neg} fill={C.neg} fillOpacity={0.15} strokeWidth={2} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export function Sparkline({ values, label }: { values: number[]; label: string }) {
  if (!values || values.length < 2) return <div className="h-10 text-xs text-slate-400">No history yet</div>;
  const data = values.map((v, i) => ({ i, v }));
  return (
    <div className="viz-root h-10" role="img" aria-label={label}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data}><Line type="monotone" dataKey="v" stroke={C.s1} strokeWidth={2} dot={false} isAnimationActive={false} />
          <YAxis hide domain={["auto", "auto"]} /></LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Multi-series indexed comparison (each portfolio indexed to 100 at range start). */
export function ComparisonChart({ series, height = 280 }: { series: { name: string; points: { session: string; nav: number }[] }[]; height?: number }) {
  const map = new Map<string, any>();
  series.forEach((s, idx) => {
    const base = s.points[0]?.nav;
    s.points.forEach((p) => {
      const row = map.get(p.session) || { session: p.session };
      row[`s${idx}`] = base ? (p.nav / base) * 100 : null;
      map.set(p.session, row);
    });
  });
  const data = Array.from(map.values()).sort((a, b) => a.session.localeCompare(b.session));
  if (!data.length) return <Empty title="No valuation history yet" />;
  return (
    <div className="viz-root" style={{ height }} role="img" aria-label="Portfolios indexed to 100 at the start of the selected range">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={C.grid} vertical={false} />
          <XAxis dataKey="session" {...axisProps} minTickGap={40} tickFormatter={(v) => day(v).replace(/, \d{4}$/, "")} />
          <YAxis {...axisProps} width={44} domain={["auto", "auto"]} />
          <Tooltip content={<TipBox fmt={(v: number) => v?.toFixed(2)} />} />
          <Legend iconType="plainline" wrapperStyle={{ fontSize: 12 }} />
          {series.map((s, i) => <Line key={s.name} type="monotone" dataKey={`s${i}`} name={s.name} stroke={SERIES[i % 5]} strokeWidth={2} dot={false} isAnimationActive={false} />)}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function ScoreBars({ components }: { components: Record<string, number> }) {
  const data = Object.entries(components || {}).map(([k, v]) => ({ k: k.replace(/_/g, " "), v }));
  if (!data.length) return null;
  return (
    <div className="viz-root" style={{ height: 28 * data.length + 20 }} role="img" aria-label="Score components out of 100">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ left: 0, right: 28, top: 0, bottom: 0 }}>
          <XAxis type="number" domain={[0, 100]} hide />
          <YAxis type="category" dataKey="k" width={150} {...axisProps} axisLine={false} />
          <Tooltip cursor={{ fill: "var(--grid)" }} content={({ active, payload }: any) => active && payload?.length ? (
            <div className="rounded bg-white px-2 py-1 text-xs shadow dark:bg-slate-900">{payload[0].payload.k}: {payload[0].value}/100</div>) : null} />
          <Bar dataKey="v" radius={[0, 4, 4, 0]} barSize={14} label={{ position: "right", fill: "var(--text-secondary)", fontSize: 11 }} isAnimationActive={false}>
            {data.map((_, i) => <Cell key={i} fill={C.s1} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
