// Which billing dates should move: the engine's ranked changes, each with the lowest left to spend before and after,
// the instruction for the biller, and a draft request to copy. The app drafts; it never sends.
import { TriangleAlert } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { money, ordinal, shortDate } from "../format";
import { Failed, Loading, Said } from "./State";
import { StatusPill } from "./Status";

interface Suggestion {
  bill_id: number;
  name: string;
  amount: number;
  from_day: number;
  to_day: number;
  low_before: number;
  low_after: number;
  improvement: number;
  instruction: string;
  draft: string;
}

interface Step {
  bill_id: number;
  name: string;
  from_day: number;
  to_day: number;
  low_after: number;
  low_date: string;
  improvement: number;
}

interface Moves {
  baseline: { low: number; low_date: string };
  suggestions: Suggestion[];
  plan: Step[];
  plan_low: number;
  movable: number;
  caveats?: string[];
  /** Why nothing is suggested, when the low point comes before any move could take effect. */
  note?: string | null;
  drafts_only: string;
  not_advice: string;
}

type Note = { text: string; tone: "info" | "error"; queued?: boolean };

export function DateMoves({ refreshKey }: { refreshKey?: number }) {
  const state = useApi<Moves>(`/api/v1/bills/suggestions?k=${refreshKey ?? 0}`);
  const { data, error } = state;
  const [copied, setCopied] = useState<number | null>(null);
  const [asking, setAsking] = useState<number | null>(null);
  const [to, setTo] = useState("");
  const [notes, setNotes] = useState<Record<number, Note>>({});
  const [queuing, setQueuing] = useState(false);
  const say = (id: number, note: Note) => setNotes((n) => ({ ...n, [id]: note }));
  async function queue(s: Suggestion) {
    if (queuing) return; // a second tap while the first is on its way would queue the same email twice
    setQueuing(true);
    try {
      await api("/api/v1/approvals", { method: "POST", body: JSON.stringify({ to, subject: `Request to move my billing date to the ${ordinal(s.to_day)}`, body: s.draft, reason: `${s.name}: ${ordinal(s.from_day)} to ${ordinal(s.to_day)}`, source: `bill:${s.bill_id}` }) });
      say(s.bill_id, { text: "", tone: "info", queued: true });
      setAsking(null);
      setTo("");
    } catch (e) {
      say(s.bill_id, { text: (e as Error).message, tone: "error" });
    } finally {
      setQueuing(false);
    }
  }
  async function copy(s: Suggestion) {
    try {
      await navigator.clipboard.writeText(`${s.instruction}\n\n${s.draft}`);
      setCopied(s.bill_id);
      setTimeout(() => setCopied(null), 2000);
    } catch {
      setCopied(null);
      say(s.bill_id, { text: "This browser would not copy. Use Send it, with approval, or try another browser.", tone: "error" });
    }
  }
  return (
    <section className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 800 }}>Dates that could move</h2>
        {data && <span style={{ fontSize: 12, color: "var(--ink-2)" }}>The lowest left to spend in the next 60 days: {money(data.baseline.low)} on {shortDate(data.baseline.low_date)}. Each move below is worked out on its own.</span>}
      </div>
      {error && !data && <Failed error={error} what="the date moves" onRetry={state.reload} card={false} />}
      {!data && !error && <Loading what="the date moves" card={false} />}
      {data?.caveats && data.caveats.length > 0 && (
        <div style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 12, color: "var(--warning-ink)", fontWeight: 600 }}>
          <TriangleAlert size={14} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} />
          <span>{data.caveats.join(" ")}</span>
        </div>
      )}
      {data && data.suggestions.length === 0 && (
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
          {data.movable === 0
            ? <>No bill is marked as one whose date can move yet. In your bills list above, tick <strong>Date can move</strong> on each bill whose biller allows a date change, then Save.</>
            : data.note || "No move lifts the lowest left to spend by a dollar or more; the dates are already as good as they get."}
        </div>
      )}
      {data && data.plan.length > 0 && (
        <div style={{ borderTop: "1px solid var(--surface-2)", paddingTop: 8, fontSize: 13, display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-start" }}>
          <span>
            <strong>Together, in this order:</strong>{" "}
            {data.plan.map((p, i) => (
              <span key={p.bill_id}>{i > 0 ? "; then " : ""}{p.name} to the {ordinal(p.to_day)} (lowest left to spend {money(p.low_after)})</span>
            ))}
            .
          </span>
          {data.plan_low < 0
            ? <StatusPill status="warn">Still {money(-data.plan_low)} short at the lowest point.</StatusPill>
            : <StatusPill status="good">The lowest point stays {money(data.plan_low)} above your <Link to="/glossary#buffer">safety cushion</Link>.</StatusPill>}
        </div>
      )}
      {data && data.suggestions.length > 0 && <div className="lab" style={{ marginTop: 4 }}>Each on its own</div>}
      {data?.suggestions.map((s) => {
        const note = notes[s.bill_id];
        return (
          <div key={s.bill_id} style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", borderTop: "1px solid var(--surface-2)", paddingTop: 8, fontSize: 13 }}>
            <div style={{ flex: "1 1 240px" }}>
              <strong>{s.name}</strong> <span style={{ color: "var(--ink-2)" }}>{money(s.amount)} · {ordinal(s.from_day)} → {ordinal(s.to_day)}</span>
              <div style={{ color: "var(--ink-2)" }}>Lowest left to spend {money(s.low_before)} → <strong style={{ color: s.low_after < 0 ? "var(--warning-ink)" : "var(--safe-ink)" }}>{money(s.low_after)}</strong>, up {money(s.improvement)}.</div>
            </div>
            <button className="btn" type="button" onClick={() => copy(s)} style={{ minHeight: 34, padding: "4px 10px" }}>{copied === s.bill_id ? "Copied" : "Copy the request"}</button>
            <button className="btn" type="button" aria-expanded={asking === s.bill_id} onClick={() => setAsking(asking === s.bill_id ? null : s.bill_id)} style={{ minHeight: 34, padding: "4px 10px" }}>Send it, with approval</button>
            {asking === s.bill_id && (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  queue(s);
                }}
                style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "flex-end", flexBasis: "100%" }}
              >
                <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 220px" }}>
                  <span className="lab">The biller's email address</span>
                  <input className="input" type="email" value={to} onChange={(e) => setTo(e.target.value)} required style={{ minHeight: 34, padding: "4px 10px" }} />
                </label>
                <button className="btn dark" type="submit" disabled={queuing} style={{ minHeight: 34, padding: "4px 10px" }}>{queuing ? "Queuing…" : "Queue for approval"}</button>
              </form>
            )}
            {note?.queued && (
              <span role="status" style={{ flexBasis: "100%", fontSize: 13, color: "var(--ink-2)" }}>
                Waiting for your OK on <Link to="/confirm">Needs you</Link>. Nothing is sent until you approve it there.
              </span>
            )}
            {note && !note.queued && <span style={{ flexBasis: "100%" }}><Said text={note.text} tone={note.tone} /></span>}
          </div>
        );
      })}
      {data && <div style={{ fontSize: 11, color: "var(--muted)" }}>{data.drafts_only} {data.not_advice}</div>}
    </section>
  );
}
