import { Link } from "react-router-dom";
import { ByCheck } from "../components/ByCheck";
import { RiverChart } from "../components/RiverChart";
import { daysWord, money, monthDay, shortDate } from "../format";
import { getPrefs } from "../prefs";
import type { ViewProps } from "./index";

function Tile({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="card" style={{ flex: "0 1 190px", padding: "12px 16px", display: "flex", flexDirection: "column", gap: 2 }}>
      <div style={{ fontSize: 12, color: "var(--ink-2)", fontWeight: 600 }}>{label}</div>
      <div className="num" style={{ fontSize: 22, fontWeight: 800, letterSpacing: "-0.01em" }}>{value}</div>
      <div style={{ fontSize: 12, color: "var(--muted)" }}>{sub}</div>
    </div>
  );
}

export function River({ snapshot }: ViewProps) {
  const s = snapshot;
  const look = getPrefs().look;
  const pay = s.next_paycheck.amount;
  const widthFor = (v: number) => `${Math.max(2, Math.min(100, (Math.abs(v) / Math.max(1, pay)) * 100))}%`;
  const before = s.due_before_payday.items;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <section style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "stretch" }}>
        <div className="card" style={{ flex: "1 1 420px", padding: "18px 22px", display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ fontSize: 13, color: "var(--ink-2)", fontWeight: 600 }}>Safe to spend through {shortDate(s.safe_to_spend.through)}</div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
            <div className="num" style={{ fontSize: 52, fontWeight: 800, letterSpacing: "-0.03em", lineHeight: 1 }}>{money(s.safe_to_spend.amount)}</div>
            <div aria-hidden="true" style={{ width: 14, height: 14, borderRadius: 4, background: "var(--safe)" }} />
          </div>
          <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
            After {s.advances.items.length} advance{s.advances.items.length === 1 ? "" : "s"} repay on payday, {s.window.items.length} bills clear, and {money(s.set_aside.total)} is set aside for the window after. The {money(s.buffer)} buffer is kept.
            {s.usual_pace.per_day > 0 && (
              <span> Your usual pace is {money(s.usual_pace.per_day)} a day, {money(s.usual_pace.per_window)} over two weeks{s.usual_pace.per_window > s.safe_to_spend.amount ? `, which is ${money(s.usual_pace.per_window - s.safe_to_spend.amount)} more than is safe` : ""}.</span>
            )}
            {s.safe_to_spend.version === "v0" && <span style={{ color: "var(--muted)" }}> First-cut arithmetic; the full engine lands in M5.</span>}
          </div>
        </div>
        <Tile
          label="Due before payday"
          value={money(s.due_before_payday.total)}
          sub={
            s.due_before_payday.groups && s.due_before_payday.groups.length > 0
              ? `${s.due_before_payday.groups.map((g) => `${g.label.toLowerCase()} ${money(g.total)}`).join(" · ")}${s.due_before_payday.next_check && s.due_before_payday.next_check.total > 0 ? ` · ${money(s.due_before_payday.next_check.total)} waits for the ${s.due_before_payday.next_check.position === "end" ? "end-of-month" : s.due_before_payday.next_check.position === "mid" ? "15th" : "next"} check` : ""}`
              : before.length
                ? `${before.filter((e) => !e.via_card).map((e) => e.name).slice(0, 2).join(", ") || "only card-charged items"} · ${shortDate(before[0].date)}`
                : "Nothing before payday"
          }
        />
        <Tile label="Cash to spend" value={money(s.cash)} sub={`${s.accounts.filter((a) => ["checking", "savings", "cash"].includes(a.kind)).length} accounts`} />
        <Link to="/confirm" style={{ textDecoration: "none", color: "inherit", display: "contents" }}>
          <Tile label="Needs you" value={String(s.pending.confirmations + s.pending.approvals)} sub={`${s.pending.confirmations} to confirm, ${s.pending.approvals} to approve`} />
        </Link>
      </section>

      <section className="card" style={{ padding: "16px 20px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", gap: 14 }}>
          <h1 style={{ margin: 0, fontSize: 16, fontWeight: 800 }}>The river: what is free to spend, day by day</h1>
          <div style={{ fontSize: 12, color: "var(--muted)" }}>Next 60 days · the floor is your {money(s.buffer)} buffer · hover any day for the detail</div>
        </div>
        <RiverChart snapshot={s} look={look} />
        <div style={{ display: "flex", flexWrap: "wrap", gap: 18, fontSize: 12, color: "var(--ink-2)", fontWeight: 600, padding: "0 4px" }}>
          <span><span className="sw" style={{ background: "var(--paycheck)" }} />Paycheck</span>
          <span><span className="sw" style={{ background: "var(--bill)" }} />Bill</span>
          <span><span className="sw" style={{ background: "var(--debt)" }} />Advance or installment</span>
          <span><span className="sw" style={{ background: "var(--safe)", opacity: 0.5 }} />At your usual pace</span>
          <span><span className="sw" style={{ background: "var(--ink-2)", height: 2, verticalAlign: 3 }} />If nothing else is spent</span>
          <span><span className="sw" style={{ background: "var(--buffer)", height: 2, verticalAlign: 3 }} />Buffer {money(s.buffer)}</span>
        </div>
      </section>

      <ByCheck s={s} />

      <section style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "stretch" }}>
        <div className="card" style={{ flex: "1 1 520px", padding: "16px 20px", display: "flex", flexDirection: "column" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 6 }}>
            <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800 }}>{s.next_paycheck.known === false ? "No paycheck on file yet" : `This paycheck · ${shortDate(s.next_paycheck.date)} · ${daysWord(s.next_paycheck.days)}`}</h2>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>every line names its source</span>
          </div>
          <div className="row"><span>Paycheck lands</span><div className="bar" style={{ width: "100%", background: "var(--paycheck)" }} /><strong className="num" style={{ textAlign: "right" }}>{money(pay, { sign: true })}</strong></div>
          <div className="row"><span>Advances repay themselves <span style={{ color: "var(--muted)" }}>· {s.advances.items.length} apps</span></span><div className="bar" style={{ width: widthFor(s.advances.total), background: "var(--debt)" }} /><span className="num" style={{ textAlign: "right" }}>{money(-s.advances.total)}</span></div>
          <div className="row"><span>Bills {monthDay(s.window.start)} to {monthDay(s.window.end)} <span style={{ color: "var(--muted)" }}>· {s.window.items.length} bills</span></span><div className="bar" style={{ width: widthFor(s.window.total), background: "var(--bill)" }} /><span className="num" style={{ textAlign: "right" }}>{money(-s.window.total)}</span></div>
          <div className="row"><span>Set aside for the window after <span style={{ color: "var(--muted)" }}>· {s.set_aside.items.length} items</span></span><div className="bar" style={{ width: widthFor(s.set_aside.total), background: "var(--surface)", border: "2px solid var(--bill)", boxSizing: "border-box" }} /><span className="num" style={{ textAlign: "right" }}>{money(-s.set_aside.total)}</span></div>
          <div className="row"><span>Buffer kept</span><div className="bar" style={{ width: widthFor(s.buffer), background: "var(--buffer)" }} /><span className="num" style={{ textAlign: "right" }}>{money(-s.buffer)}</span></div>
          <div className="row" style={{ paddingTop: 10 }}><strong>Safe to spend</strong><div className="bar" style={{ width: widthFor(s.safe_to_spend.amount), background: "var(--safe)" }} /><strong className="num" style={{ textAlign: "right", fontSize: 16 }}>{money(s.safe_to_spend.amount)}</strong></div>
        </div>
        <div style={{ flex: "1 1 320px", display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="card" style={{ padding: "14px 18px", display: "flex", flexDirection: "column", gap: 8 }}>
            <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800 }}>In the window, in order</h2>
            {s.window.items.length === 0 && <div style={{ color: "var(--muted)" }}>Nothing due. Add bills or import a statement.</div>}
            {s.window.items.map((e, i) => (
              <div key={`${e.source}-${i}`} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 13 }}>
                <span className="sw" style={{ background: e.kind === "installment" ? "var(--debt)" : "var(--bill)" }} />
                <span style={{ flexGrow: 1 }}>{e.name} <span style={{ color: "var(--muted)" }}>· {shortDate(e.date)}{e.autopay ? " · autopay" : ""}{e.via_card ? " · on the card" : ""}{e.planned ? " · planned" : ""}{e.pending ? " · payments left not set" : ""}</span></span>
                <strong className="num">{money(e.amount)}</strong>
              </div>
            ))}
          </div>
          <div className="card" style={{ padding: "14px 18px", display: "flex", flexDirection: "column", gap: 8 }}>
            <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800 }}>Don't buy</h2>
            <div style={{ color: "var(--muted)", fontSize: 13 }}>The rules that fill this list arrive with the engine in M5. Until then: nothing a fourth installment plan would fix.</div>
            <div><Link to="/transactions">See the transactions</Link></div>
          </div>
        </div>
      </section>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{s.not_advice}</div>
    </div>
  );
}
