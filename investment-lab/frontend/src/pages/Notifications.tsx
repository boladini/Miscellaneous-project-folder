import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { Badge, Button, Card, Empty, ErrorState, Loading } from "../components/ui";
import { ago, dt } from "../format";
import { useApi } from "../hooks";

const TYPES = ["proposal", "approval", "fill", "order", "exit", "drawdown", "thesis", "stale_data", "job_failure", "experiment", "review", "reconciliation", "portfolio"];

export default function Notifications() {
  const [unread, setUnread] = useState<"" | "true" | "false">("");
  const [type, setType] = useState("");
  const { data, error, loading, reload } = useApi<any>(`/notifications?limit=200${unread ? `&unread=${unread}` : ""}${type ? `&type=${type}` : ""}`, { poll: 60000 });
  const nav = useNavigate();
  async function open(n: any) {
    if (!n.read_at) await api(`/notifications/${n.id}/read`, { method: "POST" }).catch(() => {});
    if (n.link) nav(n.link); else reload();
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-xl font-bold">Notifications</h1><p className="text-sm text-slate-500">All alerts concern simulated paper trades and include the data timestamp.</p></div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-xs">Show<select value={unread} onChange={(e) => setUnread(e.target.value as any)} className="mt-0.5 block rounded-md border border-slate-300 px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-800">
            <option value="">All</option><option value="true">Unread</option><option value="false">Read</option></select></label>
          <label className="text-xs">Type<select value={type} onChange={(e) => setType(e.target.value)} className="mt-0.5 block rounded-md border border-slate-300 px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-800">
            <option value="">All types</option>{TYPES.map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}</select></label>
          <Button onClick={async () => { await api("/notifications/read-all", { method: "POST" }); reload(); }}>Mark all read</Button>
        </div>
      </div>
      <Card>
        {loading && !data ? <Loading /> : error ? <ErrorState error={error} onRetry={reload} /> : !data?.items?.length ? <Empty title="No notifications" /> : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {data.items.map((n: any) => (
              <li key={n.id}>
                <button onClick={() => open(n)} className={`flex w-full gap-3 px-1 py-3 text-left hover:bg-slate-50 dark:hover:bg-slate-800/50 ${n.read_at ? "opacity-70" : ""}`}>
                  <span aria-label={n.read_at ? "read" : "unread"} className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${n.read_at ? "bg-transparent ring-1 ring-slate-300" : "bg-blue-600"}`} />
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-2"><span className={`text-sm ${n.read_at ? "" : "font-semibold"}`}>{n.title}</span>
                      {n.severity === "urgent" && <Badge tone="bad">urgent</Badge>}{n.severity === "digest" && <Badge icon={false}>digest</Badge>}<Badge icon={false}>{n.type.replace("_", " ")}</Badge></span>
                    <span className="mt-0.5 block text-sm text-slate-600 dark:text-slate-400">{n.body}</span>
                    <span className="mt-0.5 block text-xs text-slate-400">{ago(n.created_at)}{n.data_timestamp && ` · data as of ${dt(n.data_timestamp)}`}{n.delivery_state !== "in_app" && ` · email ${n.delivery_state.replace("email_", "")}`}</span>
                  </span>
                </button>
              </li>))}
          </ul>)}
      </Card>
    </div>
  );
}
