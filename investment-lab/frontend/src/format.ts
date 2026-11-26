const TZ = "America/Chicago";

export const usd = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : v.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: digits, maximumFractionDigits: digits });

export const pct = (v: number | null | undefined, digits = 2, signed = true) =>
  v === null || v === undefined || Number.isNaN(v) ? "n/a" : `${signed && v > 0 ? "+" : ""}${(v * 100).toFixed(digits)}%`;

export const num = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "n/a" : v.toLocaleString("en-US", { maximumFractionDigits: digits });

export const dt = (iso: string | null | undefined) => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", { timeZone: TZ, month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" });
};

export const day = (iso: string | null | undefined) => {
  if (!iso) return "—";
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(Date.UTC(y, m - 1, d, 12)).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
  }
  return new Date(iso).toLocaleDateString("en-US", { timeZone: TZ, month: "short", day: "numeric", year: "numeric" });
};

export const ago = (iso: string | null | undefined) => {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 0) return `in ${humanize(-s)}`;
  return `${humanize(s)} ago`;
};

function humanize(s: number) {
  if (s < 90) return `${Math.round(s)}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  if (s < 172800) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

export const familyLabel: Record<string, string> = {
  contrarian: "Contrarian", growth: "Growth", asymmetric: "Asymmetric Bets", event_driven: "Event-Driven", ai_generated: "AI-Generated",
};

export const signClass = (v: number | null | undefined) =>
  v === null || v === undefined ? "text-slate-500" : v > 0 ? "text-emerald-700 dark:text-emerald-400" : v < 0 ? "text-rose-700 dark:text-rose-400" : "";
