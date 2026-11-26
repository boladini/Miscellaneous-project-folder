// Typed-ish API client. Cookies carry the HttpOnly session; mutations send the CSRF token header.
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public correlationId?: string) {
    super(message);
  }
}

function csrf(): string {
  const m = document.cookie.match(/(?:^|; )lab_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  const method = opts.method || "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRF-Token"] = csrf();
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, {
      method, headers, credentials: "same-origin", signal: opts.signal,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch (e: any) {
    if (e?.name === "AbortError") throw e;
    throw new ApiError(0, "network", "Cannot reach the server. Retrying automatically…");
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const err = data?.error || {};
    if (res.status === 401 && !path.startsWith("/auth/")) window.dispatchEvent(new Event("lab:unauthenticated"));
    throw new ApiError(res.status, err.code || "error", err.message || res.statusText, err.correlation_id);
  }
  return data as T;
}

export function newKey(): string {
  return (crypto as any).randomUUID ? crypto.randomUUID().replace(/-/g, "") : Math.random().toString(36).slice(2) + Date.now();
}
