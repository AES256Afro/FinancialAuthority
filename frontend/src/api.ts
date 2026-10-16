// One fetch wrapper. Sends the session cookie and the header the server requires on writes.
// In the static demo build the same calls are answered from the engine's recorded output instead.
import { useEffect, useState } from "react";
import { DEMO, demoFetch } from "./demo/shim";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (init.method && init.method.toUpperCase() !== "GET") shared.clear(); // a write makes every answer stale
  const headers = new Headers(init.headers);
  headers.set("X-Requested-With", "FinancialAuthority");
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  let response: Response;
  try {
    response = DEMO ? await demoFetch(path, init) : await fetch(path, { ...init, headers, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "The app can't be reached right now. Check the connection, then try again.");
  }
  if (!response.ok) {
    let detail = response.statusText || `The app answered ${response.status}.`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      detail = readable(body.detail) ?? detail;
    } catch {
      // not JSON
    }
    throw new ApiError(response.status, detail);
  }
  if (response.headers.get("content-type")?.includes("text/csv")) return (await response.text()) as unknown as T;
  return (await response.json()) as T;
}

/** A refusal in words: FastAPI sends a list of field problems when a form does not fit. */
function readable(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((d) => {
      const item = d as { loc?: unknown[]; msg?: string };
      const field = Array.isArray(item.loc) ? String(item.loc[item.loc.length - 1] ?? "") : "";
      return `${field ? `${field.replace(/_/g, " ")}: ` : ""}${item.msg ?? "not accepted"}`;
    });
    return parts.length ? `Check the form. ${parts.join("; ")}.` : null;
  }
  return null;
}

export interface Loaded<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

// Two components asking for the same path at once share one request; an answer is reused for a moment so a page
// that asks for it twice loads it once. Reload always asks again.
const shared = new Map<string, { at: number; promise: Promise<unknown> }>();
const SHARE_MS = 1500;

function sharedGet<T>(path: string, fresh: boolean): Promise<T> {
  const now = Date.now();
  const hit = shared.get(path);
  if (!fresh && hit && now - hit.at < SHARE_MS) return hit.promise as Promise<T>;
  const promise = api<T>(path);
  shared.set(path, { at: now, promise });
  promise.catch(() => shared.delete(path));
  return promise;
}

export function useApi<T>(path: string | null): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    setLoading(true);
    sharedGet<T>(path, tick > 0)
      .then((d) => {
        if (alive) {
          setData(d);
          setError(null);
        }
      })
      .catch((e: ApiError) => {
        if (alive) setError(e);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [path, tick]);
  return { data, error, loading, reload: () => setTick((t) => t + 1) };
}
