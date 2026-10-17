// "How is this worked out?" under safe to spend: from what the bank says is in each account to the one figure,
// step by step, then each account's share, and what is left out on purpose. Every figure is the engine's.
import { ChevronRight, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { amountClass, money, shortDate, when } from "../format";
import type { Snapshot } from "../types";
import { StatusPill } from "./Status";

/** No paycheck on file: the engine still draws its dates two weeks out so the plan has a frame, but none of them is
 * a payday, so no screen may name one as fact. */
export function noPayday(s: Snapshot): boolean {
  return s.next_paycheck.known === false;
}

/** The heading over safe to spend: "Safe to spend through Tue Oct 27", or "Safe to spend now" with no payday. */
export function safeTitle(s: Snapshot): string {
  return noPayday(s) ? "Safe to spend now" : `Safe to spend through ${shortDate(s.safe_to_spend.through)}`;
}

/** The pill beside the figure, always an icon and a word. */
export function SafePill({ s }: { s: Snapshot }) {
  const short = s.safe_to_spend.amount < 0;
  if (noPayday(s)) return <StatusPill status={short ? "crit" : "warn"}>{short ? "Short, and no payday on file" : "No payday on file"}</StatusPill>;
  return <StatusPill status={short ? "crit" : "good"}>{short ? "Short before payday" : "Bills covered"}</StatusPill>;
}

/** The sentence under the figure: what it is and what is already taken off. */
export function SafeSentence({ s }: { s: Snapshot }) {
  const cushion = <><span className="num">{money(s.buffer)}</span> <Link to="/glossary#buffer">safety cushion</Link></>;
  if (noPayday(s)) {
    return <>No payday on file, so this counts no paycheck: it is what checking holds after the bills due by {shortDate(s.safe_to_spend.through)} and your {cushion}. <Link to="/paystubs#paycheck" style={{ fontWeight: 700 }}>Add your paycheck</Link></>;
  }
  return <>What you can spend until payday on {shortDate(s.next_paycheck.date)}. Already taken off: bills due before then, and your {cushion}. Bills after payday come out of that paycheck.</>;
}

/** What the plan does not know yet, said under the figure it affects (the engine's own caveats). */
export function PlanCaveats({ s }: { s: Snapshot }) {
  const list = s.caveats ?? [];
  if (list.length === 0) return null;
  return (
    <div role="note" style={{ display: "flex", flexDirection: "column", gap: 4, padding: "8px 10px", borderRadius: 8, background: "var(--warning-soft)", color: "var(--warning-ink)", fontSize: 12, fontWeight: 600 }}>
      {list.map((c) => (
        <span key={c} className={amountClass(c)} style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
          <TriangleAlert size={13} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} />{c}
        </span>
      ))}
    </div>
  );
}

export function SafeExplain({ s, open = false }: { s: Snapshot; open?: boolean }) {
  const [isOpen, setOpen] = useState(open);
  const safe = s.safe_to_spend;
  if (!safe.steps) return null;
  const several = (safe.by_account ?? []).length > 1;
  return (
    <details open={open} onToggle={(e) => setOpen(e.currentTarget.open)} style={{ fontSize: 13 }}>
      {/* The arrow turns down when open, so open and closed look different. */}
      <summary style={{ cursor: "pointer", fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 4, color: "var(--ink-2)", minHeight: 32 }}>
        <ChevronRight size={14} aria-hidden style={{ transform: isOpen ? "rotate(90deg)" : "none", transition: "transform .15s" }} />How is this worked out?
      </summary>
      <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 8 }}>
        {s.fresh?.balances_as_of && (
          <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Balances as of {when(s.fresh.balances_as_of)}, the oldest report among your accounts.</div>
        )}
        <div style={{ overflowX: "auto" }}>
          <table className="ledger" style={{ maxWidth: 560 }}>
            <tbody>
              {safe.steps.map((st) => (
                <tr key={st.label}>
                  <td>
                    {st.label}
                    {(st.note || st.as_of) && <div style={{ fontSize: 11, color: "var(--muted)" }}>{[st.note, st.as_of ? when(st.as_of) : ""].filter(Boolean).join(", ")}</div>}
                    {st.items && st.items.length > 0 && (
                      <div className="num" style={{ fontSize: 11, color: "var(--muted)" }}>{st.items.slice(0, 4).map((i) => `${i.name} ${money(i.amount, { cents: true })} on ${shortDate(i.date)}`).join(" · ")}{st.items.length > 4 ? ` · and ${st.items.length - 4} more` : ""}</div>
                    )}
                  </td>
                  <td className="num" style={{ textAlign: "right", whiteSpace: "nowrap", verticalAlign: "top" }}>{st.amount < 0 ? "− " : ""}{money(Math.abs(st.amount), { cents: true })}</td>
                </tr>
              ))}
              <tr>
                <td><strong>{safeTitle(s)}</strong></td>
                <td className="num" style={{ textAlign: "right", fontWeight: 800, whiteSpace: "nowrap" }}>{money(safe.amount, { cents: true })}</td>
              </tr>
            </tbody>
          </table>
        </div>
        {several && (
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <strong style={{ fontSize: 12 }}>By account</strong>
            <div style={{ overflowX: "auto" }}>
              <table className="ledger" style={{ maxWidth: 560 }}>
                <thead><tr><th>Account</th><th style={{ textAlign: "right" }}>Has</th><th style={{ textAlign: "right" }}>Goes out</th><th style={{ textAlign: "right" }}>Safe</th></tr></thead>
                <tbody>
                  {(safe.by_account ?? []).map((a) => (
                    <tr key={a.id}>
                      <td>{a.name}{a.paycheck_lands ? <div style={{ fontSize: 11, color: "var(--muted)" }}>the paycheck lands here; it keeps the safety cushion</div> : null}</td>
                      <td className="num" style={{ textAlign: "right", whiteSpace: "nowrap" }}>{money(a.balance, { cents: true })}</td>
                      <td className="num" style={{ textAlign: "right", whiteSpace: "nowrap" }}>{money(a.goes_out, { cents: true })}</td>
                      <td className="num" style={{ textAlign: "right", fontWeight: 800, whiteSpace: "nowrap" }}>{money(a.safe, { cents: true })}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        {(safe.left_out ?? []).length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <strong style={{ fontSize: 12 }}>Not counted, on purpose</strong>
            <ul style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 2, color: "var(--ink-2)" }}>
              {(safe.left_out ?? []).map((x) => <li key={x.name}><strong>{x.name}</strong> {x.verb ?? (x.owes ? "owes" : "has")} <span className="num">{money(x.amount, { cents: true })}</span>. <span className={amountClass(x.why)}>{x.why}</span></li>)}
            </ul>
          </div>
        )}
      </div>
    </details>
  );
}
