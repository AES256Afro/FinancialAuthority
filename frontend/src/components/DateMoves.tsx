// Which billing dates should move: the engine's ranked changes, each with the low point before and after,
// the instruction for the biller, and a draft request to copy. The app drafts; it never sends.
import { useState } from "react";
import { api, useApi } from "../api";
import { money, shortDate } from "../format";

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
  drafts_only: string;
  not_advice: string;
}

function ordinal(n: number): string {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return n + (s[(v - 20) % 10] ?? s[v] ?? s[0]);
}

export function DateMoves({ refreshKey }: { refreshKey?: number }) {
  const { data, error } = useApi<Moves>(`/api/v1/bills/suggestions?k=${refreshKey ?? 0}`);
  const [copied, setCopied] = useState<number | null>(null);
  const [asking, setAsking] = useState<number | null>(null);
  const [to, setTo] = useState("");
  const [queued, setQueued] = useState<string | null>(null);
  async function queue(s: Suggestion) {
    try {
      await api("/api/v1/approvals", { method: "POST", body: JSON.stringify({ to, subject: `Request to move my billing date to the ${ordinal(s.to_day)}`, body: s.draft, reason: `${s.name}: ${ordinal(s.from_day)} to ${ordinal(s.to_day)}`, source: `bill:${s.bill_id}` }) });
      setQueued(`Queued for your approval on Needs you. Nothing is sent until you approve it there.`);
      setAsking(null);
      setTo("");
    } catch (e) {
      setQueued((e as Error).message);
    }
  }
  async function copy(s: Suggestion) {
    try {
      await navigator.clipboard.writeText(`${s.instruction}\n\n${s.draft}`);
      setCopied(s.bill_id);
      setTimeout(() => setCopied(null), 2000);
    } catch {
      setCopied(null);
    }
  }
  return (
    <section className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <strong style={{ fontSize: 14 }}>Dates that could move</strong>
        {data && <span style={{ fontSize: 12, color: "var(--ink-2)" }}>The sixty-day low point is {money(data.baseline.low)} on {shortDate(data.baseline.low_date)}. Each change is judged alone.</span>}
      </div>
      {error && <div style={{ fontSize: 13, color: "var(--critical)" }}>{error.message}</div>}
      {data && data.suggestions.length === 0 && <div style={{ fontSize: 13, color: "var(--muted)" }}>{data.movable === 0 ? "No bill is marked movable yet. Mark the ones whose biller allows a date change." : "No move lifts the low point by a dollar or more; the dates are already as good as they get."}</div>}
      {data && data.plan.length > 0 && (
        <div style={{ borderTop: "1px solid var(--surface-2)", paddingTop: 8, fontSize: 13 }}>
          <strong>Together, in this order:</strong>{" "}
          {data.plan.map((p, i) => (
            <span key={p.bill_id}>{i > 0 ? "; then " : ""}{p.name} to the {ordinal(p.to_day)} (low point {money(p.low_after)})</span>
          ))}
          . <span style={{ color: data.plan_low < 0 ? "var(--warning)" : "var(--safe)", fontWeight: 700 }}>{data.plan_low < 0 ? `Still ${money(-data.plan_low)} short at the worst point.` : `The worst point stays ${money(data.plan_low)} above the buffer.`}</span>
        </div>
      )}
      {data && data.suggestions.length > 0 && <div className="lab" style={{ marginTop: 4 }}>Each on its own</div>}
      {data?.suggestions.map((s) => (
        <div key={s.bill_id} style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", borderTop: "1px solid var(--surface-2)", paddingTop: 8, fontSize: 13 }}>
          <div style={{ flex: "1 1 240px" }}>
            <strong>{s.name}</strong> <span style={{ color: "var(--ink-2)" }}>{money(s.amount)} · {ordinal(s.from_day)} → {ordinal(s.to_day)}</span>
            <div style={{ color: "var(--ink-2)" }}>Low point {money(s.low_before)} → <strong style={{ color: s.low_after < 0 ? "var(--warning)" : "var(--safe)" }}>{money(s.low_after)}</strong>, up {money(s.improvement)}.</div>
          </div>
          <button className="btn" type="button" onClick={() => copy(s)} style={{ minHeight: 34, padding: "4px 10px" }}>{copied === s.bill_id ? "Copied" : "Copy the request"}</button>
          <button className="btn" type="button" onClick={() => setAsking(asking === s.bill_id ? null : s.bill_id)} style={{ minHeight: 34, padding: "4px 10px" }}>Send it, with approval</button>
          {asking === s.bill_id && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                queue(s);
              }}
              style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", flexBasis: "100%" }}
            >
              <input className="input" type="email" value={to} onChange={(e) => setTo(e.target.value)} placeholder="the biller's address" required style={{ flex: "1 1 220px", minHeight: 34, padding: "4px 10px" }} aria-label="Recipient" />
              <button className="btn dark" type="submit" style={{ minHeight: 34, padding: "4px 10px" }}>Queue for approval</button>
            </form>
          )}
        </div>
      ))}
      {queued && <div style={{ fontSize: 13, color: "var(--ink-2)" }}>{queued}</div>}
      {data && <div style={{ fontSize: 11, color: "var(--muted)" }}>{data.drafts_only} {data.not_advice}</div>}
    </section>
  );
}
