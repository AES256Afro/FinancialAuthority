import type { ReactNode } from "react";
import { OctagonAlert } from "lucide-react";
import { Link } from "react-router-dom";
import { ByCheck } from "../components/ByCheck";
import { RiverChart } from "../components/RiverChart";
import { Watchlist } from "../components/Watchlist";
import { amountClass, daysWord, money, shortDate } from "../format";
import { getPrefs } from "../prefs";
import type { Window } from "../types";
import type { ViewProps } from "./index";
import { PlanCaveats, SafeExplain, SafePill, SafeSentence, noPayday, safeTitle } from "../components/SafeExplain";

function Tile({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="card" style={{ flex: "0 1 190px", padding: "12px 16px", display: "flex", flexDirection: "column", gap: 2 }}>
      <div style={{ fontSize: 12, color: "var(--ink-2)", fontWeight: 600 }}>{label}</div>
      <div className="num" style={{ fontSize: 22, fontWeight: 800, letterSpacing: "-0.01em" }}>{value}</div>
      <div className={amountClass(sub)} style={{ fontSize: 12, color: "var(--muted)" }}>{sub}</div>
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

// The next paycheck, step by step from the engine's own window: what lands, what checking carries in (or is already
// short), what leaves, and what that check leaves. The lines add up to the bottom line, which is the engine's
// figure for that check (safe to spend, above, is what checking holds until payday: a different figure).
function ThisPaycheck({ w }: { w: Window }) {
  const scale = Math.max(1, w.inflow, w.paycheck); // drawing only: the widest bar is the most that comes in
  const widthFor = (v: number) => `${Math.max(2, Math.min(100, (Math.abs(v) / scale) * 100))}%`;
  const short = w.safe < 0;
  const muted = { color: "var(--muted)" };
  return (
    <>
      <Line label="Paycheck lands" bar={<div className="bar" style={{ width: widthFor(w.paycheck), background: "var(--paycheck)" }} />} figure={<strong className="num" style={{ textAlign: "right" }}>{money(w.paycheck, { sign: true })}</strong>} />
      {w.carried_in > 0 && <Line label={<>Carried in from checking <span style={muted}>· after what is due before payday and the safety cushion</span></>} bar={<div className="bar" style={{ width: widthFor(w.carried_in), background: "var(--paycheck)", opacity: 0.55 }} />} figure={<span className="num" style={{ textAlign: "right" }}>{money(w.carried_in, { sign: true })}</span>} />}
      {w.carried_in < 0 && <Line label={<strong style={{ color: "var(--critical-ink)" }}><OctagonAlert size={13} aria-hidden="true" style={{ verticalAlign: -2, marginRight: 4 }} />Checking is already short before payday</strong>} bar={<div className="bar" style={{ width: widthFor(w.carried_in), background: "var(--critical-soft)", border: "2px solid var(--critical)", boxSizing: "border-box" }} />} figure={<span className="num" style={{ textAlign: "right", color: "var(--critical-ink)" }}>{money(w.carried_in)}</span>} />}
      {w.advances.total > 0 && <Line label={<>Advance apps take back <span style={muted}>· {w.advances.items.length} {w.advances.items.length === 1 ? "app" : "apps"}</span></>} bar={<div className="bar" style={{ width: widthFor(w.advances.total), background: "var(--debt)" }} />} figure={<span className="num" style={{ textAlign: "right" }}>{money(-w.advances.total)}</span>} />}
      <Line label={<>Bills {shortDate(w.start)} to {shortDate(w.end)} <span style={muted}>· {w.bills.items.length} bills</span></>} bar={<div className="bar" style={{ width: widthFor(w.bills.total), background: "var(--bill)" }} />} figure={<span className="num" style={{ textAlign: "right" }}>{money(-w.bills.total)}</span>} />
      <Line label={<>Kept for next paycheck's bills <span style={muted}>· {w.set_aside.items.length} items</span></>} bar={<div className="bar" style={{ width: widthFor(w.set_aside.total), background: "var(--surface)", border: "2px solid var(--bill)", boxSizing: "border-box" }} />} figure={<span className="num" style={{ textAlign: "right" }}>{money(-w.set_aside.total)}</span>} />
      <Line top last label={<strong>{short ? <><OctagonAlert size={14} aria-hidden="true" style={{ verticalAlign: -2, marginRight: 4, color: "var(--critical-ink)" }} />Short from the {shortDate(w.start)} check</> : `Left from the ${shortDate(w.start)} check`}</strong>} bar={<div className="bar" style={{ width: widthFor(w.safe), background: short ? "var(--critical)" : "var(--safe)" }} />} figure={<strong className="num" style={{ textAlign: "right", fontSize: 16, color: short ? "var(--critical-ink)" : undefined }}>{money(w.safe)}</strong>} />
      <div style={{ fontSize: 12, color: "var(--ink-2)", paddingTop: 6 }}>What this check leaves through {shortDate(w.end)}, before everyday spending. Safe to spend, at the top, is a different figure: what checking holds until payday.</div>
    </>
  );
}

export function River({ snapshot }: ViewProps) {
  const s = snapshot;
  const look = getPrefs().look;
  const before = s.due_before_payday.items;
  const spendingAccounts = s.accounts.filter((a) => ["checking", "cash"].includes(a.kind)).length;
  const first = noPayday(s) ? undefined : s.windows[0];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <section style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "stretch" }}>
        <div className="card" style={{ flex: "1 1 420px", padding: "18px 22px", display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ fontSize: 13, color: "var(--ink-2)", fontWeight: 600 }}>{safeTitle(s)}</div>
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <div className="num" style={{ fontSize: 52, fontWeight: 800, letterSpacing: "-0.03em", lineHeight: 1 }}>{money(s.safe_to_spend.amount)}</div>
            <SafePill s={s} />
          </div>
          <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
            <SafeSentence s={s} />
            {s.usual_pace.per_day > 0 && (
              <span className="num"> You usually spend {money(s.usual_pace.per_day)} a day, about {money(s.usual_pace.per_window)} between paychecks{s.usual_pace.over_safe > 0 ? `, which is ${money(s.usual_pace.over_safe)} more than is safe` : ""}.</span>
            )}
          </div>
          <PlanCaveats s={s} />
          <SafeExplain s={s} />
        </div>
        <Tile
          label={noPayday(s) ? `Due by ${shortDate(s.safe_to_spend.through)}` : "Due before payday"}
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
          <div style={{ fontSize: 12, color: "var(--muted)" }}>Next 60 days · the floor line is your <span className="num">{money(s.buffer)}</span> safety cushion</div>
        </div>
        <RiverChart snapshot={s} look={look} />
        <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 18px", fontSize: 12, color: "var(--ink-2)", fontWeight: 600, padding: "0 4px" }}>
          <span><span className="sw" style={{ background: "var(--paycheck)" }} />Paycheck</span>
          <span><span className="sw" style={{ background: "var(--bill)" }} />Bill</span>
          <span><span className="sw" style={{ background: "var(--debt)" }} />Advance or installment</span>
          <span><span className="sw" style={{ background: "var(--safe)", opacity: 0.5 }} />At your usual spending</span>
          <span><span className="sw" style={{ background: "var(--ink-2)", height: 2, verticalAlign: 3 }} />If nothing else is spent</span>
          <span><span className="sw" style={{ background: "var(--buffer)", height: 2, verticalAlign: 3 }} />Safety cushion <span className="num">{money(s.buffer)}</span></span>
        </div>
      </section>

      <ByCheck s={s} />

      <section style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "stretch" }}>
        <div className="card" style={{ flex: "1 1 520px", padding: "16px 20px", display: "flex", flexDirection: "column" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap", marginBottom: 6 }}>
            <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800 }}>{first ? `This paycheck · ${shortDate(first.start)} · ${daysWord(s.next_paycheck.days)}` : "No paycheck on file yet"}</h2>
            {first && <span style={{ fontSize: 12, color: "var(--muted)" }}>every line names its source</span>}
          </div>
          {first ? <ThisPaycheck w={first} /> : (
            <div style={{ fontSize: 13, color: "var(--ink-2)", display: "flex", flexDirection: "column", gap: 8, alignItems: "flex-start" }}>
              <span>With no paycheck on file there is no check to follow yet. Add it once and every screen plans around it.</span>
              <Link to="/paystubs#paycheck" className="btn dark" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", minHeight: 36 }}>Add your paycheck</Link>
            </div>
          )}
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
