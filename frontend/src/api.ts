// One fetch wrapper. Sends the session cookie and the header the server requires on writes.
// In the static demo build the same calls are answered from the engine's recorded output instead.
import { useCallback, useEffect, useRef, useState } from "react";
import { DEMO, demoFetch } from "./demo/shim";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// A write anywhere changes the plan: every answer kept is dropped, and the screens that show the plan (the header,
// Today, Home, the Needs you badge) are told to ask again once the write is done.
const WROTE = "fa-wrote";
let writeCount = 0;
// A few POSTs only work something out or try a connection (a preview, a test send, a scenario); they change
// nothing, so they do not ask the plan again.
const READ_ONLY_POST = /\/(preview|test|catalog|letter|drill)$|\/join\/check$|\/scenarios\/run$/;

/** Calls `fn` after any write through `api()` succeeds. Returns the way to stop listening. */
export function onWrite(fn: () => void): () => void {
  window.addEventListener(WROTE, fn);
  return () => window.removeEventListener(WROTE, fn);
}

// A session that ends while a screen is open (signed out elsewhere, expired): the next answer is a 401, and every
// screen would otherwise keep showing what it last loaded with a "not signed in" note under it. The app is told, so
// it asks who is signed in again and shows the sign-in page. Sign-in's own calls answer 401 for a wrong password,
// so they are not a sign that the session ended.
const SIGNED_OUT = "fa-signed-out";
const SIGN_IN_CALL = /^\/api\/auth\/(me|login|join|passkeys\/login)(\/|$)/;

/** Calls `fn` when a call answers 401 mid-session. Returns the way to stop listening. */
export function onSignedOut(fn: () => void): () => void {
  window.addEventListener(SIGNED_OUT, fn);
  return () => window.removeEventListener(SIGNED_OUT, fn);
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const writes = Boolean(init.method && init.method.toUpperCase() !== "GET");
  if (writes) shared.clear(); // a write makes every answer stale
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
    if (response.status === 401 && !DEMO && !SIGN_IN_CALL.test(path.split("?")[0])) window.dispatchEvent(new Event(SIGNED_OUT));
    throw new ApiError(response.status, detail);
  }
  if (writes && !READ_ONLY_POST.test(path.split("?")[0])) {
    writeCount += 1;
    window.dispatchEvent(new Event(WROTE));
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

/** `live`: ask again a moment after any write in the app, and when the tab is shown again after being hidden, so
 * an answer the whole app reads (the plan) never stays behind a change made on another screen. What is on screen
 * stays until the new answer arrives. */
export function useApi<T>(path: string | null, opts: { live?: boolean } = {}): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const [tick, setTick] = useState(0);
  const live = Boolean(opts.live && path);
  const askedAfter = useRef(-1); // how many writes had finished when the answer on screen was asked for
  useEffect(() => {
    if (!live) return;
    let timer: number | undefined;
    // Several writes in a row ask once; a screen that already asked again after the write is not asked twice.
    const again = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        if (askedAfter.current !== writeCount) setTick((t) => t + 1);
      }, 250);
    };
    const shown = () => {
      if (document.visibilityState === "visible") setTick((t) => t + 1);
    };
    const stop = onWrite(again);
    document.addEventListener("visibilitychange", shown);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", shown);
      window.clearTimeout(timer);
    };
  }, [live]);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    askedAfter.current = writeCount;
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
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}
