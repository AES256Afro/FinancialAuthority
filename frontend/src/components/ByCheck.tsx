// What each check pays, at a glance: one bar per check, segmented into advances, the card payment (itself
// split into the balance carried, the bills charged to the card and the expected card spend), the other bills,
// what is kept for the next paycheck, and what is left. Under each bar, the biggest lines. Every figure is the
// engine's; the bar only draws it.
import { OctagonAlert } from "lucide-react";
import { Link } from "react-router-dom";
import { eventDay, money, shortDate } from "../format";
import { cashFigure, noBalance, noPayday } from "./SafeExplain";
import { palette } from "../looks";
import { inkOn } from "../paychecks";
import { getPrefs } from "../prefs";
import type { Event, Snapshot } from "../types";

interface Part {
  label: string;
  amount: number;
  fill: string;
  title?: string;
  /** A soft fill with an edge and its own ink, for the parts that are not a solid colour of the look. */
  border?: string;
  ink?: string;
}

// The card payment's three parts read as one payment in three parts, all in the card colour of the look: the
// balance carried is solid, the bills charged to the card are its soft fill with a solid edge, and the usual card
// spend is an outline with a dashed edge.
function Bar({ parts, total }: { parts: Part[]; total: number }) {
  const shown = parts.filter((p) => p.amount > 0);
  const base = Math.max(1, total);
  return (
    <div style={{ display: "flex", height: 22, borderRadius: 6, overflow: "hidden", background: "var(--surface-2)", gap: 2 }} role="img" aria-label={shown.map((p) => `${p.label} ${money(p.amount)}`).join(", ")}>
      {shown.map((p) => (
        <div key={p.label} title={p.title ?? `${p.label}: ${money(p.amount)}`} style={{ width: `${Math.max(1.5, (p.amount / base) * 100)}%`, background: p.fill, border: p.border, boxSizing: "border-box", display: "flex", alignItems: "center", paddingLeft: 6, overflow: "hidden", whiteSpace: "nowrap", fontSize: 11, fontWeight: 800, color: p.ink ?? inkOn(p.fill) }}>
          {p.amount / base > 0.12 ? <span className="num">{p.label} {money(p.amount)}</span> : ""}
        </div>
      ))}
    </div>
  );
}

function Lines({ items, c }: { items: Event[]; c: ReturnType<typeof palette> }) {
  const top = [...items].sort((a, b) => b.amount - a.amount).slice(0, 6);
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(220px, 100%), 1fr))", gap: "4px 14px", fontSize: 12 }}>
      {top.map((e, i) => (
        <div key={`${e.source}-${e.date}-${i}`} style={{ display: "flex", gap: 6, alignItems: "baseline", minWidth: 0 }}>
          <span className="sw" style={{ background: e.kind === "card_payment" ? c.debt : e.kind === "advance" ? c.later : e.kind === "installment" ? c.bill : e.planned ? c.paycheck : c.bill, margin: 0, flexShrink: 0 }} />
          <span style={{ flexGrow: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={e.name}>
            {e.name} <span style={{ color: "var(--muted)" }}>· {eventDay(e)}{e.via_card ? " · charged to the card" : ""}{e.planned ? " · already sent" : ""}{e.pending ? " · end date not set" : ""}</span>
            {e.kind === "card_payment" && e.parts && <span className="num" style={{ color: "var(--muted)" }}> · carried {money(e.parts.balance)} + bills {money(e.parts.bills)} + usual spend {money(e.parts.spend)}</span>}
          </span>
          <strong className="num" style={{ whiteSpace: "nowrap" }}>{money(e.amount)}</strong>
        </div>
      ))}
    </div>
  );
}

export function ByCheck({ s }: { s: Snapshot }) {
  const c = palette(getPrefs().look);
  const before = s.due_before_payday;
  const groups = before.groups ?? [];
  const beforeParts: Part[] = [
    ...groups.map((g) => ({ label: g.label.toLowerCase(), amount: g.total, fill: g.key === "planned" ? c.paycheck : g.key === "advance" ? c.later : g.key === "installment" ? c.debt : c.bill })),
    { label: "safety cushion", amount: s.buffer, fill: c.buffer },
    { label: "safe to spend", amount: s.safe_to_spend.amount === null ? 0 : Math.max(0, s.safe_to_spend.amount), fill: c.safe },
  ];
  const beforeItems = before.items.filter((e) => !e.via_card);
  return (
    <section className="card" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>What each check pays</h2>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>First: what is due before your next paycheck, paid from checking. Then: what each paycheck pays once it lands. Card payments are split into their parts.</span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
          <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>{noPayday(s) ? "Now" : "Before your next paycheck"}</h3>
          <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{noPayday(s) ? "bills due by" : "through"} {shortDate(s.safe_to_spend.through)} · {noBalance(s) ? "no balance from your bank yet" : <>from <span className="num">{cashFigure(s)}</span> in checking</>}</span>
          <span className="num" style={{ marginLeft: "auto", fontWeight: 800 }}>{money(before.total)} due · {noBalance(s) || s.safe_to_spend.amount === null ? "no balance yet" : `${money(s.safe_to_spend.amount)} safe`}</span>
        </div>
        <Bar parts={beforeParts} total={s.cash === null ? before.total + s.buffer : Math.max(s.cash, before.total + s.buffer)} />
        <Lines items={beforeItems} c={c} />
        {s.on_card && s.on_card.total > 0 && <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Charged to the card, paid when you pay the card{s.on_card.paid_on ? ` on ${shortDate(s.on_card.paid_on)}` : ""}: <span className="num">{money(s.on_card.total)}</span> ({s.on_card.items.map((e) => e.name).slice(0, 4).join(", ")}).</div>}
      </div>

      {/* With no paycheck on file the engine's windows are a frame, not checks: none is drawn as a check. */}
      {noPayday(s) && <div style={{ fontSize: 13, color: "var(--ink-2)", borderTop: "1px solid var(--surface-2)", paddingTop: 10 }}>No paycheck is on file yet, so there is no check to split. <Link to="/paystubs#paycheck" style={{ fontWeight: 700 }}>Add your paycheck</Link></div>}
      {!noPayday(s) && s.windows.slice(0, 3).map((w) => {
        // No balance yet: the first check's carried-in and what it leaves are unknown (null), never short.
        const carriedIn = w.carried_in;
        const shortIn = carriedIn !== null && carriedIn < 0 && !noBalance(s) ? -carriedIn : 0;
        const safe = w.safe;
        const carried = w.bills.card_parts.balance;
        const rode = w.bills.card_parts.bills;
        const spend = w.bills.card_parts.spend;
        const parts: Part[] = [
          { label: "already short before payday", amount: shortIn, fill: "var(--critical-soft)", border: "2px solid var(--critical)", ink: "var(--critical-ink)", title: `Checking is already short before this check lands: ${money(shortIn)}` },
          { label: "advance apps", amount: w.advances.total, fill: c.later },
          { label: "card balance", amount: carried, fill: c.debt, title: `Card balance carried into this check: ${money(carried)}` },
          { label: "bills on the card", amount: rode, fill: "var(--debt-soft)", border: `2px solid ${c.debt}`, ink: "var(--debt-ink)", title: `Bills charged to the card before this check pays it: ${money(rode)}` },
          { label: "usual card spend", amount: spend, fill: c.surface, border: `2px dashed ${c.debt}`, ink: "var(--debt-ink)", title: `Everyday spending expected on the card until this check pays it: ${money(spend)}` },
          { label: "bills", amount: w.bills.other_total, fill: c.bill },
          { label: "kept for the next paycheck", amount: w.set_aside.total, fill: c.buffer },
          { label: "left", amount: safe === null ? 0 : Math.max(0, safe), fill: c.safe },
        ];
        const inflow = w.inflow;
        return (
          <div key={w.start} style={{ display: "flex", flexDirection: "column", gap: 6, borderTop: "1px solid var(--surface-2)", paddingTop: 10 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
              <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>The {shortDate(w.start)} check</h3>
              <span style={{ fontSize: 12, color: "var(--ink-2)" }}><span className="num">{money(w.paycheck)} lands{carriedIn !== null && carriedIn > 0 ? ` + ${money(carriedIn)} carried in` : ""}</span>{shortIn > 0 && <strong className="num" style={{ color: "var(--critical-ink)" }}> − {money(shortIn)} already short before payday</strong>} · pays through {shortDate(w.end)}</span>
              <span className="num" style={{ marginLeft: "auto", fontWeight: 800, display: "inline-flex", alignItems: "center", gap: 4, color: safe !== null && safe < 0 ? "var(--critical-ink)" : "inherit" }}>
                {money(w.out)} out ·{" "}
                {safe === null ? "no balance yet" : safe < 0 ? <><OctagonAlert size={14} aria-hidden="true" />Short {money(-safe)}</> : `${money(safe)} left`}
              </span>
            </div>
            <Bar parts={parts} total={Math.max(inflow ?? 0, parts.reduce((t, p) => t + p.amount, 0))} />
            <Lines items={w.bills.items} c={c} />
          </div>
        );
      })}
      <div style={{ fontSize: 11, color: "var(--muted)" }}>Left: what this paycheck has after its bills and what it keeps for the next one, before everyday spending. A card payment is paid by the check it lands on.</div>
    </section>
  );
}
