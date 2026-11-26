import { FormEvent, useState } from "react";
import { api } from "../api";
import { Button } from "../components/ui";

export default function Login({ onLogin }: { onLogin: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await api("/auth/login", { method: "POST", body: { email, password } });
      onLogin();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <h1 className="text-xl font-bold">Investment Lab</h1>
        <p className="mt-1 text-sm text-slate-500">An AI-run experimental fund. Paper trading only — no real money, no brokerage.</p>
        <label className="mt-5 block text-sm font-medium">Email
          <input autoComplete="username" type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-800" />
        </label>
        <label className="mt-3 block text-sm font-medium">Password
          <input autoComplete="current-password" type="password" required value={password} onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-800" />
        </label>
        {err && <p role="alert" className="mt-3 text-sm text-rose-700 dark:text-rose-400">✕ {err}</p>}
        <Button type="submit" variant="primary" className="mt-5 w-full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</Button>
      </form>
    </main>
  );
}
