// The phone home: the brief, in the order it matters this morning, from the engine's own sentences.
import { Link } from "react-router-dom";
import { PurchaseCheck } from "../components/PurchaseCheck";
import { Goals } from "../components/Goals";
import { NextStepsCard } from "../components/NextSteps";
import { Watchlist } from "../components/Watchlist";
import { money, shortDate } from "../format";
import type { Snapshot } from "../types";

const DOT = { info: "var(--paycheck)", warning: "var(--warning)", critical: "var(--critical)" };

export function Today({ snapshot }: { snapshot: Snapshot }) {
  const s = snapshot;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 640 }}>
      <section className="card" style={{ padding: "18px 18px 14px", display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ fontSize: 13, color: "var(--ink-2)", fontWeight: 700 }}>Safe to spend through {shortDate(s.safe_to_spend.through)}</div>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
          <div className="num" style={{ fontSize: 54, fontWeight: 800, letterSpacing: "-0.03em", lineHeight: 1 }}>{money(s.safe_to_spend.amount)}</div>
          <div aria-hidden="true" style={{ width: 14, height: 14, borderRadius: 4, background: s.safe_to_spend.amount < 0 ? "var(--critical)" : "var(--safe)" }} />
        </div>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
          Advances take {money(s.advances.total)} first, then {s.window.items.length} bills, then {money(s.set_aside.total)} set aside for the window after. Buffer {money(s.buffer)} kept.
          {s.usual_pace.per_day > 0 ? ` Your usual pace is ${money(s.usual_pace.per_day)} a day.` : ""}
        </div>
      </section>

      <section className="card" style={{ padding: "4px 16px 6px" }}>
        {s.brief.map((b, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 0", borderTop: i === 0 ? 0 : "1px solid var(--surface-2)" }}>
            <span className="sw" style={{ background: DOT[b.severity], flexShrink: 0 }} />
            <div style={{ flexGrow: 1, fontSize: 14, fontWeight: b.severity === "info" ? 500 : 700 }}>{b.text}</div>
            {b.link && b.link !== "/today" && <Link to={b.link} className="btn" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", minHeight: 36, padding: "6px 10px" }} aria-label="Open">→</Link>}
          </div>
        ))}
      </section>

      <NextStepsCard />

      <PurchaseCheck />

      <Watchlist />

      <Goals />

      <section className="card" style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 6 }}>
        <strong style={{ fontSize: 14 }}>Cash today</strong>
        {s.accounts.filter((a) => ["checking", "savings", "cash"].includes(a.kind)).map((a) => (
          <div key={a.id} style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
            <span>{a.name} <span style={{ color: "var(--muted)" }}>· {a.institution}</span></span>
            <strong className="num">{a.balance === null ? "—" : money(a.balance)}</strong>
          </div>
        ))}
        <div style={{ fontSize: 12 }}><Link to="/">Open the home view</Link></div>
      </section>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{s.not_advice}</div>
    </div>
  );
}
