import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";

/** Fetch with loading/error state, optional polling, and graceful reconnect on transient failures. */
export function useApi<T = any>(path: string | null, opts: { poll?: number } = {}) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(!!path);
  const [tick, setTick] = useState(0);
  const failures = useRef(0);

  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (!path) return;
    const ctl = new AbortController();
    let timer: number | undefined;
    setLoading((l) => l || data === null);
    api<T>(path, { signal: ctl.signal })
      .then((d) => { setData(d); setError(null); failures.current = 0; })
      .catch((e) => {
        if (e?.name === "AbortError") return;
        setError(e);
        failures.current += 1;
        if (e instanceof ApiError && (e.status === 0 || e.status >= 500)) {
          timer = window.setTimeout(reload, Math.min(30000, 2000 * 2 ** failures.current));
        }
      })
      .finally(() => setLoading(false));
    if (opts.poll) {
      const id = window.setInterval(() => { if (!document.hidden) reload(); }, opts.poll);
      return () => { ctl.abort(); window.clearInterval(id); if (timer) window.clearTimeout(timer); };
    }
    return () => { ctl.abort(); if (timer) window.clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, tick, opts.poll]);

  return { data, error, loading, reload, setData };
}

export function useJobWatcher(onDone?: () => void) {
  const [job, setJob] = useState<any>(null);
  useEffect(() => {
    if (!job || ["succeeded", "dead", "failed"].includes(job.state)) return;
    const id = window.setInterval(async () => {
      try {
        const j = await api(`/jobs/${job.id}`);
        setJob(j);
        if (["succeeded", "dead"].includes(j.state)) onDone?.();
      } catch { /* keep polling */ }
    }, 2500);
    return () => window.clearInterval(id);
  }, [job, onDone]);
  return { job, watch: (id: string, state = "queued") => setJob({ id, state }) };
}
