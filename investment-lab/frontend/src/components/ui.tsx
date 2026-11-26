import { ReactNode, useEffect, useId, useRef, useState } from "react";
import { ApiError } from "../api";

export function Card({ children, className = "", title, actions, id }: { children: ReactNode; className?: string; title?: ReactNode; actions?: ReactNode; id?: string }) {
  return (
    <section id={id} className={`rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900 ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
          <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100">{title}</h2>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

type Tone = "neutral" | "good" | "warn" | "bad" | "info" | "accent";
const TONES: Record<Tone, string> = {
  neutral: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  good: "bg-emerald-50 text-emerald-800 ring-emerald-200 dark:bg-emerald-950 dark:text-emerald-300 dark:ring-emerald-900",
  warn: "bg-amber-50 text-amber-900 ring-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:ring-amber-900",
  bad: "bg-rose-50 text-rose-800 ring-rose-200 dark:bg-rose-950 dark:text-rose-300 dark:ring-rose-900",
  info: "bg-sky-50 text-sky-800 ring-sky-200 dark:bg-sky-950 dark:text-sky-300 dark:ring-sky-900",
  accent: "bg-indigo-50 text-indigo-800 ring-indigo-200 dark:bg-indigo-950 dark:text-indigo-300 dark:ring-indigo-900",
};
const ICONS: Record<Tone, string> = { neutral: "•", good: "✓", warn: "!", bad: "✕", info: "i", accent: "★" };

/** Status pill: always icon + text, never color alone. */
export function Badge({ tone = "neutral", children, icon = true, title }: { tone?: Tone; children: ReactNode; icon?: boolean; title?: string }) {
  return (
    <span title={title} className={`inline-flex max-w-full items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ring-transparent ${TONES[tone]}`}>
      {icon && <span aria-hidden="true" className="font-bold">{ICONS[tone]}</span>}
      {children}
    </span>
  );
}

export const stateTone = (s: string): Tone =>
  ({ pending: "info", approved: "good", active: "good", filled: "good", succeeded: "good", supported: "good", ok: "good", completed: "neutral",
     rejected: "neutral", expired: "neutral", withdrawn: "neutral", closed: "neutral", legacy: "warn", paused: "warn", closing: "warn",
     blocked: "bad", failed: "bad", dead: "bad", invalidated: "bad", challenged: "warn", queued: "info", running: "accent", untested: "neutral",
     inconclusive: "neutral", unresolved: "bad", degraded: "warn", unconfigured: "warn", unavailable: "warn", cancelled: "neutral" } as Record<string, Tone>)[s] || "neutral";

export function StateBadge({ state }: { state: string }) {
  return <Badge tone={stateTone(state)}>{state.replace(/_/g, " ")}</Badge>;
}

export function Button({ children, onClick, variant = "secondary", disabled, type = "button", className = "", ariaLabel }: {
  children: ReactNode; onClick?: () => void; variant?: "primary" | "secondary" | "danger" | "ghost" | "yes" | "no"; disabled?: boolean;
  type?: "button" | "submit"; className?: string; ariaLabel?: string;
}) {
  const v = {
    primary: "bg-blue-600 text-white hover:bg-blue-700 focus-visible:outline-blue-600",
    secondary: "bg-white text-slate-800 ring-1 ring-inset ring-slate-300 hover:bg-slate-50 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700 dark:hover:bg-slate-700",
    danger: "bg-rose-600 text-white hover:bg-rose-700 focus-visible:outline-rose-600",
    ghost: "text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800",
    yes: "bg-emerald-600 text-white hover:bg-emerald-700 focus-visible:outline-emerald-600 text-base px-6 py-2.5",
    no: "bg-white text-slate-800 ring-1 ring-inset ring-slate-300 hover:bg-slate-100 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-600 text-base px-6 py-2.5",
  }[variant];
  return (
    <button type={type} aria-label={ariaLabel} disabled={disabled} onClick={onClick}
      className={`inline-flex min-h-[40px] items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-50 ${v} ${className}`}>
      {children}
    </button>
  );
}

export function Stat({ label, value, sub, tip, valueClass = "" }: { label: string; value: ReactNode; sub?: ReactNode; tip?: string; valueClass?: string }) {
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-1 text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {label}{tip && <InfoTip text={tip} />}
      </div>
      <div className={`mt-1 truncate text-xl font-semibold tabular-nums ${valueClass}`}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{sub}</div>}
    </div>
  );
}

export function InfoTip({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <span className="relative inline-flex">
      <button type="button" aria-describedby={open ? id : undefined} aria-label="More information" onClick={() => setOpen(!open)}
        onBlur={() => setOpen(false)} onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}
        className="inline-flex h-4 w-4 items-center justify-center rounded-full bg-slate-200 text-[10px] font-bold normal-case text-slate-600 dark:bg-slate-700 dark:text-slate-300">?</button>
      {open && (
        <span role="tooltip" id={id} className="absolute left-1/2 top-5 z-30 w-64 -translate-x-1/2 rounded-md bg-slate-900 p-2 text-xs font-normal normal-case tracking-normal text-white shadow-lg dark:bg-slate-700">
          {text}
        </span>
      )}
    </span>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 p-6 text-sm text-slate-500">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-blue-600" aria-hidden="true" />{label}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: ApiError | Error | null; onRetry?: () => void }) {
  if (!error) return null;
  const e = error as ApiError;
  return (
    <div role="alert" className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-900 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-200">
      <div className="font-semibold">✕ {e.message || "Something went wrong"}</div>
      {e.correlationId && <div className="mt-1 text-xs opacity-80">Correlation ID: {e.correlationId}</div>}
      {onRetry && <Button className="mt-2" onClick={onRetry}>Retry</Button>}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 p-6 text-center dark:border-slate-700">
      <div className="text-sm font-semibold text-slate-700 dark:text-slate-200">{title}</div>
      {children && <div className="mx-auto mt-1 max-w-prose text-sm text-slate-500 dark:text-slate-400">{children}</div>}
    </div>
  );
}

export function Tabs({ tabs, value, onChange, label }: { tabs: { id: string; label: ReactNode }[]; value: string; onChange: (v: string) => void; label: string }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  return (
    <div role="tablist" aria-label={label} className="flex gap-1 overflow-x-auto border-b border-slate-200 dark:border-slate-800">
      {tabs.map((t, i) => (
        <button key={t.id} ref={(el) => (refs.current[i] = el)} role="tab" aria-selected={value === t.id} tabIndex={value === t.id ? 0 : -1}
          onClick={() => onChange(t.id)}
          onKeyDown={(e) => {
            if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
              const n = (i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length;
              onChange(tabs[n].id);
              refs.current[n]?.focus();
            }
          }}
          className={`whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium ${value === t.id ? "border-blue-600 text-blue-700 dark:text-blue-400" : "border-transparent text-slate-600 hover:text-slate-900 dark:text-slate-400"}`}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Modal({ open, title, children, onClose }: { open: boolean; title: string; children: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("button, input, textarea")?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); prev?.focus(); };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/50 p-4 sm:items-center" onClick={onClose}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}
        className="w-full max-w-lg rounded-xl bg-white p-5 shadow-xl dark:bg-slate-900">
        <h2 className="text-lg font-semibold">{title}</h2>
        <div className="mt-3">{children}</div>
      </div>
    </div>
  );
}

/** Confirmation that requires typing a word (for destructive admin actions). */
export function TypedConfirm({ open, title, word, body, onConfirm, onClose, busy }: {
  open: boolean; title: string; word: string; body: ReactNode; onConfirm: () => void; onClose: () => void; busy?: boolean;
}) {
  const [v, setV] = useState("");
  useEffect(() => { if (open) setV(""); }, [open]);
  return (
    <Modal open={open} title={title} onClose={onClose}>
      <div className="text-sm text-slate-600 dark:text-slate-300">{body}</div>
      <label className="mt-3 block text-sm">Type <code className="rounded bg-slate-100 px-1 dark:bg-slate-800">{word}</code> to confirm
        <input value={v} onChange={(e) => setV(e.target.value)} className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-800" />
      </label>
      <div className="mt-4 flex justify-end gap-2">
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="danger" disabled={v !== word || busy} onClick={onConfirm}>{busy ? "Working…" : "Confirm"}</Button>
      </div>
    </Modal>
  );
}

export const RANGES = [
  { id: "1M", days: 31 }, { id: "3M", days: 92 }, { id: "YTD", days: -1 }, { id: "1Y", days: 366 }, { id: "ALL", days: 0 },
];

export function rangeStart(id: string): string | null {
  const r = RANGES.find((x) => x.id === id);
  if (!r || r.days === 0) return null;
  const d = new Date();
  if (r.days === -1) return `${d.getFullYear()}-01-01`;
  d.setDate(d.getDate() - r.days);
  return d.toISOString().slice(0, 10);
}

export function RangeFilter({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div role="radiogroup" aria-label="Date range" className="inline-flex rounded-lg bg-slate-100 p-0.5 dark:bg-slate-800">
      {RANGES.map((r) => (
        <button key={r.id} role="radio" aria-checked={value === r.id} onClick={() => onChange(r.id)}
          className={`rounded-md px-2.5 py-1 text-xs font-semibold ${value === r.id ? "bg-white text-slate-900 shadow-sm dark:bg-slate-600 dark:text-white" : "text-slate-600 dark:text-slate-300"}`}>
          {r.id}
        </button>
      ))}
    </div>
  );
}

export function Table({ head, rows, empty = "Nothing to show yet.", caption }: { head: ReactNode[]; rows: ReactNode[][]; empty?: string; caption?: string }) {
  if (!rows.length) return <Empty title={empty} />;
  return (
    <div className="-mx-4 overflow-x-auto px-4">
      <table className="w-full min-w-[560px] text-left text-sm">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800 dark:text-slate-400">
            {head.map((h, i) => <th key={i} scope="col" className="px-2 py-2 font-medium">{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-slate-100 last:border-0 dark:border-slate-800/70">
              {r.map((c, j) => <td key={j} className="px-2 py-2 align-top tabular-nums">{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Kv({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
      {items.map(([k, v], i) => (
        <div key={i} className="flex justify-between gap-3 border-b border-slate-100 py-1 dark:border-slate-800">
          <dt className="shrink-0 text-slate-500 dark:text-slate-400">{k}</dt>
          <dd className="min-w-0 break-words text-right font-medium tabular-nums">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Toast({ message, tone = "good", onClose }: { message: string | null; tone?: Tone; onClose: () => void }) {
  useEffect(() => { if (message) { const t = setTimeout(onClose, 6000); return () => clearTimeout(t); } }, [message, onClose]);
  if (!message) return null;
  return (
    <div role="status" aria-live="polite" className="fixed bottom-20 left-1/2 z-50 w-[calc(100%-2rem)] max-w-md -translate-x-1/2 md:bottom-6">
      <div className={`rounded-lg px-4 py-3 text-sm shadow-lg ring-1 ${TONES[tone]}`}>{ICONS[tone]} {message}</div>
    </div>
  );
}

export function SyntheticNote() {
  return <Badge tone="warn" title="Demo mode: generated data, not real market observations or research">SYNTHETIC DEMO DATA</Badge>;
}
