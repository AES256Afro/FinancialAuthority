// "How is this worked out?" under safe to spend: from what the bank says is in each account to the one figure,
// step by step, then each account's share, and what is left out on purpose. Every figure is the engine's.
import { ChevronDown } from "lucide-react";
import { money, shortDate } from "../format";
import type { Snapshot } from "../types";

function when(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export function SafeExplain({ s, open = false }: { s: Snapshot; open?: boolean }) {
  const safe = s.safe_to_spend;
  if (!safe.steps) return null;
  const several = (safe.by_account ?? []).length > 1;
  return (
    <details open={open} style={{ fontSize: 13 }}>
      <summary style={{ cursor: "pointer", fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 4, color: "var(--ink-2)" }}>
        <ChevronDown size={14} aria-hidden />How is this worked out?
      </summary>
      <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 8 }}>
        <table className="ledger" style={{ maxWidth: 560 }}>
          <tbody>
            {safe.steps.map((st) => (
              <tr key={st.label}>
                <td>
                  {st.label}
                  {(st.note || st.as_of) && <div style={{ fontSize: 11, color: "var(--muted)" }}>{[st.note, st.as_of ? when(st.as_of) : ""].filter(Boolean).join(", ")}</div>}
                  {st.items && st.items.length > 0 && (
                    <div style={{ fontSize: 11, color: "var(--muted)" }}>{st.items.slice(0, 4).map((i) => `${i.name} ${money(i.amount, { cents: true })} on ${shortDate(i.date)}`).join(" · ")}{st.items.length > 4 ? ` · and ${st.items.length - 4} more` : ""}</div>
                  )}
                </td>
                <td className="num" style={{ textAlign: "right", whiteSpace: "nowrap", verticalAlign: "top" }}>{st.amount < 0 ? "− " : ""}{money(Math.abs(st.amount), { cents: true })}</td>
              </tr>
            ))}
            <tr>
              <td><strong>Safe to spend through {shortDate(safe.through)}</strong></td>
              <td className="num" style={{ textAlign: "right", fontWeight: 800 }}>{money(safe.amount, { cents: true })}</td>
            </tr>
          </tbody>
        </table>
        {several && (
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <strong style={{ fontSize: 12 }}>By account</strong>
            <table className="ledger" style={{ maxWidth: 560 }}>
              <thead><tr><th>Account</th><th style={{ textAlign: "right" }}>Has</th><th style={{ textAlign: "right" }}>Goes out</th><th style={{ textAlign: "right" }}>Safe</th></tr></thead>
              <tbody>
                {(safe.by_account ?? []).map((a) => (
                  <tr key={a.id}>
                    <td>{a.name}{a.paycheck_lands ? <div style={{ fontSize: 11, color: "var(--muted)" }}>the paycheck lands here; it keeps the buffer</div> : null}</td>
                    <td className="num" style={{ textAlign: "right" }}>{money(a.balance, { cents: true })}</td>
                    <td className="num" style={{ textAlign: "right" }}>{money(a.goes_out, { cents: true })}</td>
                    <td className="num" style={{ textAlign: "right", fontWeight: 800 }}>{money(a.safe, { cents: true })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {(safe.left_out ?? []).length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <strong style={{ fontSize: 12 }}>Not counted, on purpose</strong>
            <ul style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 2, color: "var(--ink-2)" }}>
              {(safe.left_out ?? []).map((x) => <li key={x.name}><strong>{x.name}</strong> {x.verb ?? (x.owes ? "owes" : "has")} {money(x.amount, { cents: true })}. {x.why}</li>)}
            </ul>
          </div>
        )}
      </div>
    </details>
  );
}
