// Bill board: every bill period is a card that moves right as it is paid. A card that stops moving is the problem.
import { amountClass, money, shortDate } from "../format";
import type { Event } from "../types";
import type { ViewProps } from "./index";
import { PlanCaveats, SafeExplain, noPayday, safeFigure, safeShort } from "../components/SafeExplain";
import { SeverityTag, StatusPill } from "../components/Status";

const SOURCE_WORDS: Record<string, string> = {
  bill: "your bills list",
  series: "a repeating charge",
  plan: "a pay-later plan",
  advance: "an advance app",
  debt: "your debts",
  planned: "a payment you sent",
  payment: "a payment",
  payments: "a payment",
  paid: "your transactions",
  txn: "your transactions",
  transaction: "your transactions",
  account: "an account",
  card: "a card",
  income: "your paycheck",
  paystub: "a paystub",
  fact: "your mail",
  statement: "a statement",
};

/** Where a line came from, in words: "bill:12" is "your bills list". */
export function sourceWord(source: string): string {
  const kind = source.split(":")[0];
  return SOURCE_WORDS[kind] ?? kind.replace(/_/g, " ");
}

type Funding = "funded" | "not_yet" | "past_due" | "unmatched" | "paid";
const FUNDING: Record<Funding, { severity: string; word: string }> = {
  funded: { severity: "good", word: "Funded" },
  not_yet: { severity: "warning", word: "Not yet funded" },
  past_due: { severity: "critical", word: "Past due" },
  unmatched: { severity: "warning", word: "Not matched yet" },
  paid: { severity: "good", word: "Paid" },
};

function Card({ title, amount, when, src, funding, note, muted }: { title: string; amount: number; when: string; src: string; funding?: Funding; note?: string; muted?: boolean }) {
  return (
    <div className="card" style={{ padding: "10px 12px", display: "flex", flexDirection: "column", gap: 4, opacity: muted ? 0.75 : 1 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8, fontWeight: 800 }}>
        <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>{title}</span>
        <span className="num">{money(amount)}</span>
      </div>
      <div style={{ fontSize: 12, color: "var(--ink-2)", display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}><span className={amountClass(when)}>{when}</span><span>from {src}</span></div>
      {funding && <div><SeverityTag severity={FUNDING[funding].severity} word={FUNDING[funding].word} /></div>}
      {note && <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{note}</div>}
    </div>
  );
}

function Column({ title, total, bg, children, empty }: { title: string; total: number; bg?: string; children: React.ReactNode; empty: string }) {
  const count = Array.isArray(children) ? children.filter(Boolean).length : children ? 1 : 0;
  return (
    <div style={{ background: bg ?? "var(--surface-2)", borderRadius: 14, padding: 12, display: "flex", flexDirection: "column", gap: 8, minHeight: 320 }}>
      <h3 className="lab" style={{ margin: "0 0 2px", display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8, fontSize: 13 }}><span>{title}</span><span className="num">{money(total)}</span></h3>
      {count === 0 ? <div style={{ fontSize: 12, color: "var(--muted)", padding: "6px 2px" }}>{empty}</div> : children}
    </div>
  );
}

export function BillBoard({ snapshot }: ViewProps) {
  const w = snapshot.windows[0];
  // The columns are the engine's (snapshot.board): the same rows its totals add up, so a bill late before payday
  // sits in one column, not two.
  const board = snapshot.board;
  const due7 = board.due_7.items;
  const later = board.later.items;
  const nextWindow = w?.set_aside.items ?? [];
  const paid = board.paid.items;
  const problems = board.problems.items;
  const fundingOf = (e: Event): Funding => (e.funded === false ? "not_yet" : "funded");
  const short = safeShort(snapshot);
  // With no paycheck on file the window is a frame two weeks out, not a paycheck: the columns say so.
  const unknown = noPayday(snapshot);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 16 }}>
        <h2 style={{ margin: 0, fontSize: 22, fontWeight: 800, letterSpacing: "-0.01em" }}>Bill board</h2>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Every bill is a card that moves right as it gets paid. A card that stops moving is the problem.</div>
        <div style={{ flexGrow: 1 }} />
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", background: "var(--surface)", border: "1px solid var(--hairline)", borderRadius: 999, padding: "6px 14px" }}>
          <strong className="num" style={{ color: short ? "var(--critical-ink)" : undefined }}>{safeFigure(snapshot)}</strong>
          <span style={{ fontSize: 12, color: "var(--ink-2)" }}>safe to spend</span>
          {short && <StatusPill status="crit">Short before payday</StatusPill>}
        </div>
      </div>
      <PlanCaveats s={snapshot} />
      <SafeExplain s={snapshot} />
      <section className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5" style={{ gap: 12 }}>
        <Column title="Past due or unmatched" total={board.problems.total} bg={problems.length ? "var(--critical-soft)" : undefined} empty="Nothing. Keep the column empty.">
          {problems.map((p) => <Card key={p.source} title={p.name} amount={p.amount} when={shortDate(p.due_date)} src={sourceWord(p.source)} funding={p.status === "past_due" ? "past_due" : "unmatched"} note={p.status === "late" ? "If you paid it, your transactions catch up at the next sync." : undefined} />)}
        </Column>
        <Column title="Due in 7 days" total={board.due_7.total} bg="var(--warning-soft)" empty="Nothing due this week.">
          {due7.map((e, i) => <Card key={`${e.source}-${i}`} title={e.name} amount={e.amount} when={`${shortDate(e.date)} · ${e.autopay ? "autopay" : "pay it yourself"}`} src={sourceWord(e.source)} funding={fundingOf(e)} />)}
        </Column>
        <Column title={unknown ? "Due later" : "Due later this paycheck"} total={board.later.total} empty={unknown ? "Nothing more in the next weeks." : "Nothing more before the next paycheck."}>
          {later.map((e, i) => <Card key={`${e.source}-${i}`} title={e.name} amount={e.amount} when={`${shortDate(e.date)} · ${e.autopay ? "autopay" : "pay it yourself"}`} src={sourceWord(e.source)} funding={fundingOf(e)} />)}
        </Column>
        <Column title={unknown ? "Kept for the weeks after" : "Kept for the next paycheck"} total={w?.set_aside.total ?? 0} empty="Nothing kept for it yet.">
          {/* Each card shows what is kept for it now (half of a big bill), so the cards add up to the column's total. */}
          {nextWindow.map((e, i) => <Card key={`${e.source}-${i}`} title={e.name} amount={e.kept ?? e.amount} when={`${shortDate(e.date)} · ${(e.share ?? 1) < 1 ? `half of ${money(e.amount)} kept` : "all kept"}`} src={sourceWord(e.source)} funding={e.funded ? "funded" : "not_yet"} />)}
        </Column>
        <Column title="Paid recently" total={board.paid.total} bg="var(--surface-2)" empty="No payments matched yet; your transactions need a few weeks of history.">
          {paid.map((p) => <Card key={p.source} title={p.name} amount={p.amount} when={`${shortDate(p.paid_date ?? p.due_date)} · matched to your transactions`} src="your transactions" funding="paid" muted />)}
        </Column>
      </section>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{snapshot.not_advice}</div>
    </div>
  );
}
