import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ByCheck } from "../components/ByCheck";
import { RiverChart } from "../components/RiverChart";
import { StatusPill } from "../components/Status";
import { Watchlist } from "../components/Watchlist";
import { daysWord, money, shortDate } from "../format";
import { getPrefs } from "../prefs";
import type { ViewProps } from "./index";
import { SafeExplain } from "../components/SafeExplain";

function Tile({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="card" style={{ flex: "0 1 190px", padding: "12px 16px", display: "flex", flexDirection: "column", gap: 2 }}>
      <div style={{ fontSize: 12, color: "var(--ink-2)", fontWeight: 600 }}>{label}</div>
      <div className="num" style={{ fontSize: 22, fontWeight: 800, letterSpacing: "-0.01em" }}>{value}</div>
      <div style={{ fontSize: 12, color: "var(--muted)" }}>{sub}</div>
    </div>
  );
}

// One row of the paycheck: on a phone the words take their own line and the bar and figure the next, so
// neither is squeezed at 375px or at the largest text size; from the sm breakpoint up they share one line.
function Line({ label, bar, figure, last, top }: { label: ReactNode; bar: ReactNode; figure: ReactNode; last?: boolean; top?: boolean }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] sm:grid-cols-[minmax(0,1fr)_minmax(0,160px)_auto]" style={{ gap: "4px 12px", alignItems: "center", padding: top ? "10px 0 7px" : "7px 0", borderBottom: last ? 0 : "1px solid var(--surface-2)" }}>
      <span className="col-span-2 sm:col-span-1">{label}</span>
      {bar}
      {figure}
    </div>
  );
}

export function River({ snapshot }: ViewProps) {
  const s = snapshot;
  const look = getPrefs().look;
  const pay = s.next_paycheck.amount;
  const widthFor = (v: number) => `${Math.max(2, Math.min(100, (Math.abs(v) / Math.max(1, pay)) * 100))}%`;
  const before = s.due_before_payday.items;
  const short = s.safe_to_spend.amount < 0;
  const spendingAccounts = s.accounts.filter((a) => ["checking", "cash"].includes(a.kind)).length;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <section style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "stretch" }}>
        <div className="card" style={{ flex: "1 1 420px", padding: "18px 22px", display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ fontSize: 13, color: "var(--ink-2)", fontWeight: 600 }}>Safe to spend through {shortDate(s.safe_to_spend.through)}</div>
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <div className="num" style={{ fontSize: 52, fontWeight: 800, letterSpacing: "-0.03em", lineHeight: 1 }}>{money(s.safe_to_spend.amount)}</div>
            <StatusPill status={short ? "crit" : "good"}>{short ? "Short before payday" : "Bills covered"}</StatusPill>
          </div>
          <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
            What you can spend until payday on {shortDate(s.next_paycheck.date)}. Already taken off: bills due before then, and your {money(s.buffer)} <Link to="/glossary#buffer">safety cushion</Link>. Bills after payday come out of that paycheck.
            {s.usual_pace.per_day > 0 && (
              <span> You usually spend {money(s.usual_pace.per_day)} a day, about {money(s.usual_pace.per_window)} between paychecks{s.usual_pace.over_safe > 0 ? `, which is ${money(s.usual_pace.over_safe)} more than is safe` : ""}.</span>
            )}
          </div>
          <SafeExplain s={s} />
        </div>
        <Tile
          label="Due before payday"
          value={money(s.due_before_payday.total)}
          sub={
            s.due_before_payday.groups && s.due_before_payday.groups.length > 0
              ? `${s.due_before_payday.groups.map((g) => `${g.label.toLowerCase()} ${money(g.total)}`).join(" · ")}${s.due_before_payday.next_check && s.due_before_payday.next_check.total > 0 ? ` · ${money(s.due_before_payday.next_check.total)} waits for the ${shortDate(s.due_before_payday.next_check.start)} check` : ""}`
              : before.length
                ? `${before.filter((e) => !e.via_card).map((e) => e.name).slice(0, 2).join(", ") || "only items charged to a card"} · ${shortDate(before[0].date)}`
                : "Nothing before payday"
          }
        />
        <Tile label="In checking now" value={money(s.cash)} sub={s.savings ? `savings of ${money(s.savings)} kept apart` : `${spendingAccounts} account${spendingAccounts === 1 ? "" : "s"}`} />
        <Link to="/confirm" style={{ textDecoration: "none", color: "inherit", display: "contents" }}>
          <Tile label="Needs you" value={String(s.pending.total)} sub={`${s.pending.confirmations} to confirm, ${s.pending.approvals} to approve`} />
        </Link>
      </section>

      <section className="card" style={{ padding: "16px 20px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", gap: 14 }}>
          <h2 style={{ margin: 0, fontSize: 16, fontWeight: 800 }}>The river: what is free to spend, day by day</h2>
          <div style={{ fontSize: 12, color: "var(--muted)" }}>Next 60 days · the floor line is your {money(s.buffer)} safety cushion</div>
        </div>
        <RiverChart snapshot={s} look={look} />
        <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 18px", fontSize: 12, color: "var(--ink-2)", fontWeight: 600, padding: "0 4px" }}>
          <span><span className="sw" style={{ background: "var(--paycheck)" }} />Paycheck</span>
          <span><span className="sw" style={{ background: "var(--bill)" }} />Bill</span>
          <span><span className="sw" style={{ background: "var(--debt)" }} />Advance or installment</span>
          <span><span className="sw" style={{ background: "var(--safe)", opacity: 0.5 }} />At your usual spending</span>
          <span><span className="sw" style={{ background: "var(--ink-2)", height: 2, verticalAlign: 3 }} />If nothing else is spent</span>
          <span><span className="sw" style={{ background: "var(--buffer)", height: 2, verticalAlign: 3 }} />Safety cushion {money(s.buffer)}</span>
        </div>
      </section>

      <ByCheck s={s} />

      <section style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "stretch" }}>
        <div className="card" style={{ flex: "1 1 520px", padding: "16px 20px", display: "flex", flexDirection: "column" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap", marginBottom: 6 }}>
            <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800 }}>{s.next_paycheck.known === false ? "No paycheck on file yet" : `This paycheck · ${shortDate(s.next_paycheck.date)} · ${daysWord(s.next_paycheck.days)}`}</h2>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>every line names its source</span>
          </div>
          <Line label="Paycheck lands" bar={<div className="bar" style={{ width: "100%", background: "var(--paycheck)" }} />} figure={<strong className="num" style={{ textAlign: "right" }}>{money(pay, { sign: true })}</strong>} />
          <Line label={<>Advance apps take back <span style={{ color: "var(--muted)" }}>· {s.advances.items.length} apps</span></>} bar={<div className="bar" style={{ width: widthFor(s.advances.total), background: "var(--debt)" }} />} figure={<span className="num" style={{ textAlign: "right" }}>{money(-s.advances.total)}</span>} />
          <Line label={<>Bills {shortDate(s.window.start)} to {shortDate(s.window.end)} <span style={{ color: "var(--muted)" }}>· {s.window.items.length} bills</span></>} bar={<div className="bar" style={{ width: widthFor(s.window.total), background: "var(--bill)" }} />} figure={<span className="num" style={{ textAlign: "right" }}>{money(-s.window.total)}</span>} />
          <Line label={<>Kept for next paycheck's bills <span style={{ color: "var(--muted)" }}>· {s.set_aside.items.length} items</span></>} bar={<div className="bar" style={{ width: widthFor(s.set_aside.total), background: "var(--surface)", border: "2px solid var(--bill)", boxSizing: "border-box" }} />} figure={<span className="num" style={{ textAlign: "right" }}>{money(-s.set_aside.total)}</span>} />
          <Line label="Safety cushion kept" bar={<div className="bar" style={{ width: widthFor(s.buffer), background: "var(--buffer)" }} />} figure={<span className="num" style={{ textAlign: "right" }}>{money(-s.buffer)}</span>} />
          <Line top last label={<strong>Safe to spend</strong>} bar={<div className="bar" style={{ width: widthFor(s.safe_to_spend.amount), background: short ? "var(--critical)" : "var(--safe)" }} />} figure={<strong className="num" style={{ textAlign: "right", fontSize: 16, color: short ? "var(--critical-ink)" : undefined }}>{money(s.safe_to_spend.amount)}</strong>} />
        </div>
        <div style={{ flex: "1 1 320px", display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="card" style={{ padding: "14px 18px", display: "flex", flexDirection: "column", gap: 8 }}>
            <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800 }}>Bills this paycheck pays, by date</h2>
            {s.window.items.length === 0 && <div style={{ color: "var(--muted)" }}>Nothing due. <Link to="/bills">Add bills</Link> or <Link to="/settings/connectors#import">import a statement</Link>.</div>}
            {s.window.items.map((e, i) => (
              <div key={`${e.source}-${i}`} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 13 }}>
                <span className="sw" style={{ background: e.kind === "installment" ? "var(--debt)" : "var(--bill)" }} />
                <span style={{ flexGrow: 1, minWidth: 0 }}>{e.name} <span style={{ color: "var(--muted)" }}>· {shortDate(e.date)}{e.autopay ? " · autopay" : ""}{e.via_card ? " · charged to the card" : ""}{e.planned ? " · already sent" : ""}{e.pending ? " · payments left not set" : ""}</span></span>
                <strong className="num">{money(e.amount)}</strong>
              </div>
            ))}
          </div>
          <Watchlist />
        </div>
      </section>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{s.not_advice}</div>
    </div>
  );
}
