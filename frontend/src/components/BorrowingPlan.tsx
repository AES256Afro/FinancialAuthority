// The borrowing plan with its reasons (M54), picture first: the stairs, one bar per month of what the pay-later loans
// take, each loan its own colour (named in the key and in every bar's words), stepping down as loans end. Then the
// steps in the order a person would take them, each with its reason; then each loan with what it still costs in
// interest and what $100 early would save. Every figure is the engine's.
import { useState } from "react";
import { api, useApi } from "../api";
import { money, shortDate } from "../format";
import { palette } from "../looks";
import { getPrefs } from "../prefs";
import { Said, Show } from "./State";
import { SeverityTag } from "./Status";

interface Loan { id: number; name: string; label?: string; next_date: string; next_amount: number; payments_left: number; ends: string; left_to_pay: number; payoff_estimate: number; interest_ahead: number; apr: number | null; autopay: boolean | null; source: string }
interface PlanOut {
  loans: Loan[];
  stairs: { month: string; total: number; parts: { id: number; name: string; amount: number }[] }[];
  finish_line: { name: string; ends: string }[];
  steps: { kind: string; text: string }[];
  totals: { loans: number; left_to_pay: number; payoff_estimate: number; interest_ahead: number };
  not_advice: string;
}

const MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const STEP_WORD: Record<string, string> = { keep: "First", finish: "Soon", extra: "With any extra", zero: "On schedule", room: "Room" };

export function BorrowingPlan() {
  const state = useApi<PlanOut>("/api/v1/borrowing/plan", { live: true });
  const [picked, setPicked] = useState<string | null>(null);
  const [said, setSaid] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  // The owner says the next payment is made: it counts now, and the bank's own row takes its place when it comes.
  async function paid(id: number) {
    setSaid(null);
    try {
      const r = await api<{ said: string }>(`/api/v1/borrowing/plans/${id}/paid`, { method: "POST", body: JSON.stringify({}) });
      setSaid({ text: r.said, tone: "info" });
      state.reload();
    } catch (e) {
      setSaid({ text: (e as Error).message, tone: "error" });
    }
  }
  return (
    <Show state={state} what="your borrowing plan">
      {(p) => {
        if (p.loans.length === 0) return null;
        const c = palette(getPrefs().look) as Record<string, string>;
        const series = [c.housing, c.bill, c.debt, c.later, c.saving, c.safe];
        const colour = new Map(p.loans.map((l, i) => [l.id, i < series.length ? series[i] : "var(--buffer)"]));
        const top = Math.max(1, ...p.stairs.map((s) => s.total));
        const shown = p.stairs.find((s) => s.month === picked) ?? null;
        return (
          <section className="card" aria-labelledby="bplan-title" style={{ padding: "16px 18px", display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <h2 id="bplan-title" style={{ margin: 0, fontSize: 16, fontWeight: 800 }}>Your borrowing plan</h2>
              <span className="num" style={{ fontSize: 13, color: "var(--ink-2)" }}>
                {p.totals.loans} loans, {money(p.totals.left_to_pay, { cents: true })} still to pay, of which about {money(p.totals.interest_ahead, { cents: true })} is interest ahead.
              </span>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <h3 style={{ margin: 0, fontSize: 13, fontWeight: 800 }}>What the loans take each month</h3>
              <div role="img" aria-label={p.stairs.map((s) => `${s.month}: ${money(s.total, { cents: true })}`).join("; ")}
                style={{ display: "grid", gridTemplateColumns: `repeat(${p.stairs.length}, minmax(0, 1fr))`, gap: 3, alignItems: "end", height: 140 }}>
                {p.stairs.map((s) => (
                  <button key={s.month} type="button" aria-hidden="true" tabIndex={-1} onClick={() => setPicked(s.month === picked ? null : s.month)} title={`${s.month}: ${money(s.total, { cents: true })}`}
                    style={{ height: "100%", padding: 0, border: 0, background: "transparent", display: "flex", flexDirection: "column-reverse", gap: 2, cursor: "pointer", outline: picked === s.month ? "2px solid var(--ink)" : undefined, outlineOffset: 2, borderRadius: 4 }}>
                    {s.parts.map((x, i) => (
                      <span key={x.id} style={{ height: `${(x.amount / top) * 100}%`, minHeight: x.amount > 0 ? 3 : 0, background: colour.get(x.id), borderRadius: i === s.parts.length - 1 ? "4px 4px 0 0" : 0 }} />
                    ))}
                  </button>
                ))}
              </div>
              <div aria-hidden="true" style={{ display: "grid", gridTemplateColumns: `repeat(${p.stairs.length}, minmax(0, 1fr))`, gap: 3, fontSize: 10, color: "var(--muted)", textAlign: "center" }}>
                {p.stairs.map((s, i) => <span key={s.month}>{i % 2 === 0 ? MONTH[Number(s.month.slice(5, 7)) - 1] : ""}</span>)}
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 12px", fontSize: 12, color: "var(--ink-2)" }}>
                {p.loans.map((l) => (
                  <span key={l.id} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                    <span aria-hidden="true" style={{ width: 10, height: 10, borderRadius: 3, background: colour.get(l.id) }} />{l.label ?? l.name}
                  </span>
                ))}
              </div>
              <div aria-live="polite" className="num" style={{ fontSize: 13, color: "var(--ink-2)" }}>
                {shown
                  ? <><strong style={{ color: "var(--ink)" }}>{MONTH[Number(shown.month.slice(5, 7)) - 1]} {shown.month.slice(0, 4)}: {money(shown.total, { cents: true })}</strong>{shown.parts.length ? ` (${shown.parts.map((x) => `${x.name} ${money(x.amount, { cents: true })}`).join(", ")})` : ""}</>
                  : "Tap a month to see what each loan takes in it."}
              </div>
            </div>

            <ol style={{ margin: 0, paddingLeft: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 8 }}>
              {p.steps.map((s, i) => (
                <li key={i} style={{ display: "flex", gap: 8, alignItems: "flex-start", flexWrap: "wrap", fontSize: 13 }}>
                  <SeverityTag severity={s.kind === "keep" ? "warning" : s.kind === "room" ? "info" : "good"} word={`${i + 1}. ${STEP_WORD[s.kind] ?? "Then"}`} />
                  <span className="num" style={{ flex: "1 1 240px", minWidth: 0 }}>{s.text}</span>
                </li>
              ))}
            </ol>

            <div style={{ overflowX: "auto" }}>
              <table className="ledger" style={{ fontSize: 13 }}>
                <thead><tr><th>Loan</th><th>Next</th><th style={{ textAlign: "right" }}>Left</th><th style={{ textAlign: "right" }}>Interest ahead</th><th style={{ textAlign: "right" }}>Pay off today</th><th>AutoPay</th></tr></thead>
                <tbody>
                  {p.loans.map((l) => (
                    <tr key={l.id}>
                      <td><span aria-hidden="true" style={{ display: "inline-block", width: 8, height: 8, borderRadius: 2, background: colour.get(l.id), marginRight: 6 }} />{l.label ?? l.name}<div style={{ fontSize: 11, color: "var(--muted)" }}>{l.apr ? `${l.apr}% a year` : "0%"} · ends {shortDate(l.ends)}</div></td>
                      <td className="num">{shortDate(l.next_date)}<div style={{ fontSize: 11, color: "var(--muted)" }}>{money(l.next_amount, { cents: true })}</div>
                        <button type="button" className="btn" onClick={() => paid(l.id)} style={{ minHeight: 28, padding: "2px 8px", fontSize: 11, marginTop: 4, whiteSpace: "nowrap" }}>I paid it</button></td>
                      <td className="num" style={{ textAlign: "right" }}>{money(l.left_to_pay, { cents: true })}<div style={{ fontSize: 11, color: "var(--muted)" }}>{l.payments_left} payment{l.payments_left === 1 ? "" : "s"}</div></td>
                      <td className="num" style={{ textAlign: "right", color: l.interest_ahead > 0 ? "var(--critical-ink)" : "var(--ink-2)" }}>{l.interest_ahead > 0 ? money(l.interest_ahead, { cents: true }) : "none"}</td>
                      <td className="num" style={{ textAlign: "right" }}>{money(l.payoff_estimate, { cents: true })}<div style={{ fontSize: 11, color: "var(--muted)" }}>about</div></td>
                      <td>{l.autopay === true ? <SeverityTag severity="good" word="On" /> : l.autopay === false ? <SeverityTag severity="warning" word="Off" /> : <span style={{ fontSize: 12, color: "var(--muted)" }}>not said</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Said text={said?.text ?? null} tone={said?.tone} />
            <div style={{ fontSize: 12, color: "var(--muted)" }}>Paid one before your bank shows it, or with a card the app does not see? “I paid it” counts it now; the bank's own record takes its place when it arrives, never counted twice.</div>
            <div style={{ fontSize: 12, color: "var(--muted)" }}>{p.not_advice}</div>
          </section>
        );
      }}
    </Show>
  );
}
