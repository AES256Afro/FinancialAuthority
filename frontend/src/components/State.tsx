// Loading, failed and empty: the same three states on every screen, each saying what happened and what to do.
// A failure always has a Try again; an empty list always says how to fill it.
import { Inbox, Loader, OctagonAlert } from "lucide-react";
import { useEffect, useLayoutEffect, useState, type ReactNode, type RefObject } from "react";
import { Link, useLocation } from "react-router-dom";
import type { ApiError, Loaded } from "../api";
import { DEMO } from "../demo/shim";

const box = { padding: 14, display: "flex", alignItems: "flex-start", gap: 10, fontSize: 13 } as const;

export function Loading({ what = "this", card = true }: { what?: string; card?: boolean }) {
  return (
    <div role="status" aria-live="polite" className={card ? "card" : undefined} style={{ ...box, color: "var(--ink-2)" }}>
      <Loader size={16} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} />
      <span>Loading {what}…</span>
    </div>
  );
}

export function Failed({ error, what = "this", onRetry, card = true }: { error: ApiError | Error | string; what?: string; onRetry?: () => void; card?: boolean }) {
  const message = typeof error === "string" ? error : error.message;
  // In the static demo a screen the capture did not record is not a failure: a plain line says so, with no Try again.
  if (DEMO && message.startsWith("Not part of the demo")) return <Empty card={card}>{message} Your own FinancialAuthority shows {what} here.</Empty>;
  return (
    <div role="alert" className={card ? "card" : undefined} style={{ ...box, background: "var(--critical-soft)", color: "var(--critical-ink)", flexWrap: "wrap" }}>
      <OctagonAlert size={16} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} />
      <span style={{ flex: "1 1 220px", minWidth: 0 }}><strong>Couldn't load {what}.</strong> {message}</span>
      {onRetry && <button type="button" className="btn" onClick={onRetry} style={{ minHeight: 32, padding: "5px 12px" }}>Try again</button>}
    </div>
  );
}

export function Empty({ children, to, action, card = true }: { children: ReactNode; to?: string; action?: string; card?: boolean }) {
  return (
    <div className={card ? "card" : undefined} style={{ ...box, color: "var(--ink-2)", flexWrap: "wrap" }}>
      <Inbox size={16} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} />
      <span style={{ flex: "1 1 220px", minWidth: 0 }}>{children}</span>
      {to && action && <Link to={to} style={{ fontWeight: 700 }}>{action}</Link>}
    </div>
  );
}

/** The usual shape: loading, then failed with Try again, then the content (or the empty message). */
export function Show<T>({ state, what, isEmpty, empty, children }: { state: Loaded<T>; what: string; isEmpty?: (data: T) => boolean; empty?: ReactNode; children: (data: T) => ReactNode }) {
  if (state.error && !state.data) return <Failed error={state.error} what={what} onRetry={state.reload} />;
  if (!state.data) return <Loading what={what} />;
  if (isEmpty && empty && isEmpty(state.data)) return <>{empty}</>;
  return <>{children(state.data)}</>;
}

/** The width of a box in pixels, followed as it changes, so a picture can be drawn one unit per pixel and its words
 * stay readable on a phone. Starts at `start` until the box is measured. */
export function useWidth(box: RefObject<HTMLElement | null>, start = 720): number {
  const [width, setWidth] = useState(start);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => el.clientWidth && setWidth(el.clientWidth);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [box]);
  return width;
}

/** Lands on the part of the page a link named (#id): scrolls it into view once `ready` is there, and puts focus on
 * its first control, so a next step's tap ends on the exact button and the keyboard starts there too. */
export function useJumpToHash(ready: unknown = true): void {
  const { hash } = useLocation();
  useEffect(() => {
    if (!hash || !ready) return;
    const el = document.getElementById(hash.slice(1));
    if (!el) return;
    el.scrollIntoView({ block: "start" });
    const control = el.querySelector<HTMLElement>("button, a[href], input:not([type=hidden]), select, textarea, summary, [tabindex]");
    if (control) control.focus({ preventScroll: true });
    else {
      if (!el.hasAttribute("tabindex")) el.tabIndex = -1;
      el.focus({ preventScroll: true });
    }
  }, [hash, ready]);
}

/** A save's outcome, next to the button that caused it. */
export function Said({ text, tone = "info" }: { text: string | null; tone?: "info" | "error" }) {
  if (!text) return null;
  return (
    <span role={tone === "error" ? "alert" : "status"} aria-live="polite" style={{ fontSize: 12, fontWeight: 700, color: tone === "error" ? "var(--critical-ink)" : "var(--safe-ink)" }}>
      {tone === "error" ? <OctagonAlert size={13} aria-hidden="true" style={{ verticalAlign: -2, marginRight: 4 }} /> : null}
      {text}
    </span>
  );
}
