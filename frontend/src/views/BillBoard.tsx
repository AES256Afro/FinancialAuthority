// Bill board: every bill period is a card that moves right as it is paid. A card that stops moving is the problem.
import { money, parseIso, shortDate } from "../format";
import type { Event, PaymentRow } from "../types";
import type { ViewProps } from "./index";
import { SafeExplain } from "../components/SafeExplain";

function Card({ title, amount, when, src, dot, note, muted }: { title: string; amount: number; when: string; src: string; dot?: string; note?: string; muted?: boolean }) {
  return (
    <div className="card" style={{ padding: "10px 12px", display: "flex", flexDirection: "column", gap: 4, opacity: muted ? 0.75 : 1 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", fontWeight: 800 }}>
        <span>{dot && <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: dot, marginRight: 6 }} />}{title}</span>
        <span className="num">{money(amount)}</span>
      </div>
      <div style={{ fontSize: 12, color: "var(--ink-2)", display: "flex", justifyContent: "space-between", gap: 8 }}><span>{when}</span><span className="lab">{src}</span></div>
      {note && <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{note}</div>}
    </div>
  );
}

function Column({ title, total, bg, children, empty }: { title: string; total: number; bg?: string; children: React.ReactNode; empty: string }) {
  const count = Array.isArray(children) ? children.filter(Boolean).length : children ? 1 : 0;
  return (
    <div style={{ background: bg ?? "var(--surface-2)", borderRadius: 14, padding: 12, display: "flex", flexDirection: "column", gap: 8, minHeight: 320 }}>
      <h2 className="lab" style={{ margin: "0 0 2px", display: "flex", justifyContent: "space-between", alignItems: "baseline", fontSize: 13 }}><span>{title}</span><span className="num">{money(total)}</span></h2>
      {count === 0 ? <div style={{ fontSize: 12, color: "var(--muted)", padding: "6px 2px" }}>{empty}</div> : children}
    </div>
  );
}

export function BillBoard({ snapshot }: ViewProps) {
  const today = parseIso(snapshot.as_of);
  const soon = new Date(today);
  soon.setDate(today.getDate() + 7);
  const w = snapshot.windows[0];
  const upcoming: Event[] = [...snapshot.due_before_payday.items, ...(w?.bills.items ?? [])];
  const due7 = upcoming.filter((e) => parseIso(e.date) <= soon);
  const later = upcoming.filter((e) => parseIso(e.date) > soon);
  const nextWindow = w?.set_aside.items ?? [];
  const paid = snapshot.payments.paid.slice(-10).reverse();
  const problems: PaymentRow[] = [...snapshot.payments.past_due, ...snapshot.payments.late];
  const board = snapshot.board;
  const dotFor = (e: Event) => (e.funded === false ? "var(--warning)" : "var(--safe)");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 16 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, letterSpacing: "-0.01em" }}>Bill board</h1>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Every bill is a card that moves right as it gets paid. A card that stops moving is the problem.</div>
        <div style={{ flexGrow: 1 }} />
        <div style={{ display: "flex", alignItems: "center", gap: 10, background: "var(--surface)", border: "1px solid var(--hairline)", borderRadius: 999, padding: "6px 14px" }}><span className="sw" style={{ background: "var(--safe)" }} /><strong className="num">{money(snapshot.safe_to_spend.amount)}</strong><span style={{ fontSize: 12, color: "var(--ink-2)" }}>safe to spend</span></div>
        <div style={{ display: "flex", gap: 14, fontSize: 12, color: "var(--ink-2)", fontWeight: 600 }}><span><span style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: "var(--safe)", marginRight: 5 }} />funded</span><span><span style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: "var(--warning)", marginRight: 5 }} />not yet</span></div>
      </div>
      <SafeExplain s={snapshot} />
      <section className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5" style={{ gap: 12 }}>
        <Column title="Past due or unmatched" total={board.problems.total} bg={problems.length ? "var(--critical-soft)" : undefined} empty="Nothing. Keep the column empty.">
          {problems.map((p) => <Card key={p.source} title={p.name} amount={p.amount} when={`${shortDate(p.due_date)} · ${p.status === "past_due" ? "past due" : "not matched yet"}`} src="payments" dot="var(--critical)" note={p.status === "late" ? "If paid, the ledger catches up within a sync." : undefined} />)}
        </Column>
        <Column title="Due in 7 days" total={board.due_7.total} bg="var(--warning-soft)" empty="Nothing due this week.">
          {due7.map((e, i) => <Card key={`${e.source}-${i}`} title={e.name} amount={e.amount} when={`${shortDate(e.date)} · ${e.autopay ? "autopay" : "pay online"}`} src={e.source.split(":")[0]} dot={dotFor(e)} />)}
        </Column>
        <Column title="Later this window" total={board.later.total} empty="Nothing more this window.">
          {later.map((e, i) => <Card key={`${e.source}-${i}`} title={e.name} amount={e.amount} when={`${shortDate(e.date)} · ${e.autopay ? "autopay" : "pay online"}`} src={e.source.split(":")[0]} dot={dotFor(e)} />)}
        </Column>
        <Column title="Next window, set aside" total={w?.set_aside.total ?? 0} empty="Nothing set aside yet.">
          {nextWindow.map((e, i) => <Card key={`${e.source}-${i}`} title={e.name} amount={e.amount} when={`${shortDate(e.date)} · ${(e.share ?? 1) < 1 ? "half set aside" : "set aside"}`} src={e.source.split(":")[0]} dot={e.funded ? "var(--safe)" : "var(--warning)"} />)}
        </Column>
        <Column title="Paid recently" total={board.paid.total} bg="var(--surface-2)" empty="No payments matched yet; the ledger needs a few weeks of history.">
          {paid.map((p) => <Card key={p.source} title={p.name} amount={p.amount} when={`${shortDate(p.paid_date ?? p.due_date)} · matched to the ledger`} src="paid" muted />)}
        </Column>
      </section>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{snapshot.not_advice}</div>
    </div>
  );
}
