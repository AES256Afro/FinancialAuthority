// What each check pays, at a glance: one bar per check, segmented into advances, the card payment (itself
// split into the balance carried, the bills that rode the card and the expected card spend), the other bills,
// what is set aside for the window after, and what is left. Under each bar, the biggest lines. Every figure
// is the engine's; the bar only draws it.
import { money, shortDate } from "../format";
import { palette } from "../looks";
import { getPrefs } from "../prefs";
import type { Event, Snapshot } from "../types";

interface Part {
  label: string;
  amount: number;
  fill: string;
  title?: string;
}

function checkWord(iso: string): string {
  const day = Number(iso.slice(8, 10));
  return day >= 8 && day <= 22 ? "the 15th check" : "the end-of-month check";
}

function Bar({ parts, total }: { parts: Part[]; total: number }) {
  const shown = parts.filter((p) => p.amount > 0);
  const base = Math.max(1, total);
  return (
    <div style={{ display: "flex", height: 22, borderRadius: 6, overflow: "hidden", background: "var(--surface-2)", gap: 2 }} role="img" aria-label={shown.map((p) => `${p.label} ${money(p.amount)}`).join(", ")}>
      {shown.map((p) => (
        <div key={p.label} title={p.title ?? `${p.label}: ${money(p.amount)}`} style={{ width: `${Math.max(1.5, (p.amount / base) * 100)}%`, background: p.fill, display: "flex", alignItems: "center", paddingLeft: 6, overflow: "hidden", whiteSpace: "nowrap", fontSize: 11, fontWeight: 800, color: "#fff" }}>
          {p.amount / base > 0.12 ? `${p.label} ${money(p.amount)}` : ""}
        </div>
      ))}
    </div>
  );
}

function Lines({ items, c }: { items: Event[]; c: ReturnType<typeof palette> }) {
  const top = [...items].sort((a, b) => b.amount - a.amount).slice(0, 6);
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: "4px 14px", fontSize: 12 }}>
      {top.map((e, i) => (
        <div key={`${e.source}-${e.date}-${i}`} style={{ display: "flex", gap: 6, alignItems: "baseline", minWidth: 0 }}>
          <span className="sw" style={{ background: e.kind === "card_payment" ? c.debt : e.kind === "advance" ? c.debt : e.kind === "installment" ? c.bill : e.planned ? c.paycheck : c.bill, margin: 0, flexShrink: 0 }} />
          <span style={{ flexGrow: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {e.name} <span style={{ color: "var(--muted)" }}>· {shortDate(e.date)}{e.via_card ? " · on the card" : ""}{e.planned ? " · already sent" : ""}{e.pending ? " · end date not set" : ""}</span>
            {e.kind === "card_payment" && e.parts && <span style={{ color: "var(--muted)" }}> · carried {money(e.parts.balance)} + bills {money(e.parts.bills)} + usual spend {money(e.parts.spend)}</span>}
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
    ...groups.map((g) => ({ label: g.label.toLowerCase(), amount: g.total, fill: g.key === "planned" ? c.paycheck : g.key === "installment" || g.key === "advance" ? c.debt : c.bill })),
    { label: "buffer", amount: s.buffer, fill: c.buffer },
    { label: "safe to spend", amount: Math.max(0, s.safe_to_spend.amount), fill: c.safe },
  ];
  const beforeItems = before.items.filter((e) => !e.via_card);
  return (
    <section className="card" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <strong style={{ fontSize: 15 }}>What each check pays</strong>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>before the next check from what is in checking, then each check from itself; the card payment shown in its parts</span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
          <strong>Before the next check</strong>
          <span style={{ fontSize: 12, color: "var(--ink-2)" }}>through {shortDate(s.safe_to_spend.through)} · from {money(s.cash)} in checking</span>
          <span className="num" style={{ marginLeft: "auto", fontWeight: 800 }}>{money(before.total)} due · {money(s.safe_to_spend.amount)} safe</span>
        </div>
        <Bar parts={beforeParts} total={Math.max(s.cash, before.total + s.buffer)} />
        <Lines items={beforeItems} c={c} />
        {s.on_card && s.on_card.total > 0 && <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Riding the card until it is paid{s.on_card.paid_on ? ` on ${shortDate(s.on_card.paid_on)}` : ""}: {money(s.on_card.total)} ({s.on_card.items.map((e) => e.name).slice(0, 4).join(", ")}).</div>}
      </div>

      {s.windows.slice(0, 3).map((w) => {
        const card = w.bills.items.filter((e) => e.kind === "card_payment" && !e.via_card);
        const other = w.bills.items.filter((e) => e.kind !== "card_payment" && !e.via_card);
        const carried = card.reduce((t, e) => t + (e.parts?.balance ?? 0), 0);
        const rode = card.reduce((t, e) => t + (e.parts?.bills ?? 0), 0);
        const spend = card.reduce((t, e) => t + (e.parts?.spend ?? 0), 0);
        const parts: Part[] = [
          { label: "advances", amount: w.advances.total, fill: c.debt },
          { label: "card balance", amount: carried, fill: "#6b5fcf", title: `Card balance carried into this check: ${money(carried)}` },
          { label: "bills on the card", amount: rode, fill: "#8d7ff0", title: `Bills charged to the card before this check pays it: ${money(rode)}` },
          { label: "usual card spend", amount: spend, fill: "#b4abf7", title: `Everyday spending expected on the card until this check pays it: ${money(spend)}` },
          { label: "bills", amount: other.reduce((t, e) => t + e.amount, 0), fill: c.bill },
          { label: "set aside", amount: w.set_aside.total, fill: c.buffer },
          { label: "left", amount: Math.max(0, w.safe), fill: c.safe },
        ];
        const inflow = w.paycheck + w.carried_in;
        return (
          <div key={w.start} style={{ display: "flex", flexDirection: "column", gap: 6, borderTop: "1px solid var(--surface-2)", paddingTop: 10 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
              <strong>{shortDate(w.start)}, {checkWord(w.start)}</strong>
              <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{money(w.paycheck)} lands{w.carried_in > 0 ? ` + ${money(w.carried_in)} carried in` : ""} · pays through {shortDate(w.end)}</span>
              <span className="num" style={{ marginLeft: "auto", fontWeight: 800, color: w.safe < 0 ? "var(--critical)" : "inherit" }}>{money(w.bills.total + w.advances.total)} out · {money(w.safe)} left</span>
            </div>
            <Bar parts={parts} total={Math.max(inflow, parts.reduce((t, p) => t + p.amount, 0))} />
            <Lines items={w.bills.items} c={c} />
          </div>
        );
      })}
      <div style={{ fontSize: 11, color: "var(--muted)" }}>Left is what the check has after its lines and the set-aside, before everyday spending. The card payment is paid by the check it lands on.</div>
    </section>
  );
}
