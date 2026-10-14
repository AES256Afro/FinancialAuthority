// The phone home: the brief, in the order it matters this morning.
import { Link } from "react-router-dom";
import { daysWord, money, shortDate } from "../format";
import type { Snapshot } from "../types";

export function Today({ snapshot }: { snapshot: Snapshot }) {
  const s = snapshot;
  const before = s.due_before_payday.items;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 640 }}>
      <section className="card" style={{ padding: "18px 18px 14px", display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ fontSize: 13, color: "var(--ink-2)", fontWeight: 700 }}>Safe to spend through {shortDate(s.safe_to_spend.through)}</div>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
          <div className="num" style={{ fontSize: 54, fontWeight: 800, letterSpacing: "-0.03em", lineHeight: 1 }}>{money(s.safe_to_spend.amount)}</div>
          <div aria-hidden="true" style={{ width: 14, height: 14, borderRadius: 4, background: "var(--safe)" }} />
        </div>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
          Advances take {money(s.advances.total)} first, then {s.window.items.length} bills, then {money(s.set_aside.total)} set aside for the window after. Buffer {money(s.buffer)} kept.
        </div>
      </section>

      <section className="card" style={{ padding: "4px 16px 6px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 0", borderTop: 0 }}>
          <span className="sw" style={{ background: "var(--bill)" }} />
          <div style={{ flexGrow: 1 }}>
            <strong>{before.length ? `Before payday: ${money(s.due_before_payday.total)} clears` : "Nothing clears before payday"}</strong>
            <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{before.map((e) => `${e.name} ${money(e.amount)} ${e.autopay ? "autopay" : "needs a tap"}`).join(" · ")}</div>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 0", borderTop: "1px solid var(--surface-2)" }}>
          <span className="sw" style={{ background: "var(--paycheck)" }} />
          <div style={{ flexGrow: 1 }}>
            <strong>Payday {daysWord(s.next_paycheck.days)} · {money(s.next_paycheck.amount, { sign: true })}</strong>
            <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{shortDate(s.next_paycheck.date)}. The paystub is checked against it the moment it lands (M2.6).</div>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 0", borderTop: "1px solid var(--surface-2)" }}>
          <span className="sw" style={{ background: "var(--warning)" }} />
          <div style={{ flexGrow: 1 }}>
            <strong>{s.pending.confirmations + s.pending.approvals} things need you</strong>
            <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Confirmations and approvals arrive with the mail connector (M4).</div>
          </div>
        </div>
      </section>

      <section className="card" style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 6 }}>
        <strong style={{ fontSize: 14 }}>Cash today</strong>
        {s.accounts.filter((a) => ["checking", "savings", "cash"].includes(a.kind)).map((a) => (
          <div key={a.id} style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
            <span>{a.name} <span style={{ color: "var(--muted)" }}>· {a.institution}</span></span>
            <strong className="num">{a.balance === null ? "—" : money(a.balance)}</strong>
          </div>
        ))}
        <div style={{ fontSize: 12 }}><Link to="/">Open the river</Link></div>
      </section>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{s.not_advice}</div>
    </div>
  );
}
