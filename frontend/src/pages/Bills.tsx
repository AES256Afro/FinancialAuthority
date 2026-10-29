// Bills: every confirmed bill and subscription, editable in place. First the picture: the next five weeks, each bill
// on its due day and the paydays marked. Then the list: on a phone each bill is a card; from the md breakpoint up
// they sit in one table. The date mover and planned payments follow it.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { ConfirmButton } from "../components/ConfirmButton";
import { DateMoves } from "../components/DateMoves";
import { PaidQuestions, type PastDueRow } from "../components/PaidQuestions";
import { PlannedPayments } from "../components/PlannedPayments";
import { Empty, Failed, Loading, Said, Show } from "../components/State";
import { isoDay, money, monthDay, ordinal, parseIso, parseMoney, shortDate } from "../format";
import type { BillRow, Event, Snapshot } from "../types";

interface AccountRow {
  id: number;
  name: string;
  kind: string;
}

/** A bill as GET /bills sends it: the shared row, with the next date of a bill that is not monthly. */
type Bill = BillRow & { anchor_date?: string | null };
/** One answer to "which paycheck pays it", in words that fit the real pay schedule (the engine's). */
interface PaidFromChoice {
  value: string;
  words: string;
  /** The next date that check lands, for the 15th and month-end checks. */
  next?: string | null;
}
interface BillsOut {
  bills: Bill[];
  monthly_total: number;
  monthly_basis: string;
  /** One-time costs (a move-in check, a truck): counted on their day, never as monthly. */
  one_time?: { total: number; items: { bill_id: number; name: string; amount: number; date: string; source: string }[] };
  /** Bills that begin later (a lease that has not started): not in today's monthly total. */
  starting_later?: { total: number; items: { bill_id: number; name: string; amount: number; per_month: number; starts_on: string; source: string }[] };
  paid_from_choices?: PaidFromChoice[];
}

type Note = { text: string; tone: "info" | "error" } | null;

const AMOUNT_HINT = "Type an amount like 120 or 1200.50";
const DAY_HINT = "Type a due day from 1 to 31.";
const DATE_HINT = "Pick the next date it is due.";

// How often a bill comes, in the words the list and the select use. Monthly bills keep a day of the month; the rest
// keep their next date.
const CADENCES: { value: string; words: string; each: string }[] = [
  { value: "weekly", words: "Every week", each: "every week" },
  { value: "biweekly", words: "Every two weeks", each: "every two weeks" },
  { value: "monthly", words: "Every month", each: "a month" },
  { value: "quarterly", words: "Every three months", each: "every three months" },
  { value: "semiannual", words: "Every six months", each: "every six months" },
  { value: "yearly", words: "Every year", each: "a year" },
];
const eachWord = (cadence: string) => CADENCES.find((c) => c.value === cadence)?.each ?? cadence;

/** A due day typed by a person: a whole number from 1 to 31, else null. */
function dueDayOf(text: string): number | null {
  const clean = text.trim();
  if (!/^\d{1,2}$/.test(clean)) return null;
  const n = Number(clean);
  return n >= 1 && n <= 31 ? n : null;
}

/** What one bill is, in a plain line: "$240.00 a year, next due Mon Nov 9". From the saved values only. */
function billLine(b: Bill): string {
  const cadence = b.cadence || "monthly";
  const due = cadence === "monthly" ? `due on the ${ordinal(b.due_day)}` : b.anchor_date ? `next due ${shortDate(b.anchor_date)}` : "next date not set";
  return `${money(b.amount, { cents: true })} ${eachWord(cadence)}, ${due}`;
}

// ---- the picture: the next five weeks ------------------------------------------------------------------------------

const STRIP_DAYS = 35;

function dayAfter(iso: string, n: number): string {
  const d = parseIso(iso);
  d.setDate(d.getDate() + n);
  return isoDay(d);
}

// Each day is a column: a payday is a blue column, each bill a bar on its due day as tall as its amount against the
// largest bill shown (placing only: no figure is worked out here). The words below the strip say the same.
function BillStrip() {
  // Asked again after any save, so a bill changed below moves in the picture too.
  const snap = useApi<Snapshot>("/api/v1/snapshot", { live: true });
  if (snap.error && !snap.data) return <Failed error={snap.error} what="when your bills fall" onRetry={snap.reload} />;
  if (!snap.data) return <Loading what="when your bills fall" />;
  const start = snap.data.as_of;
  const days = Array.from({ length: STRIP_DAYS }, (_, i) => dayAfter(start, i));
  const end = days[days.length - 1];
  const shown = snap.data.calendar.events.filter((e) => e.date >= start && e.date <= end && (e.kind === "paycheck" || e.bill_id !== undefined));
  const byDay = new Map<string, Event[]>();
  for (const e of shown) byDay.set(e.date, [...(byDay.get(e.date) ?? []), e]);
  const bills = shown.filter((e) => e.kind !== "paycheck");
  const paydays = [...new Set(shown.filter((e) => e.kind === "paycheck").map((e) => e.date))];
  const top = Math.max(1, ...bills.map((e) => e.amount));
  const dueOn = (d: string) => (byDay.get(d) ?? []).filter((e) => e.kind !== "paycheck");
  const said = [
    paydays.length ? `Paydays: ${paydays.map(shortDate).join(", ")}.` : "No payday in these five weeks.",
    ...days.filter((d) => dueOn(d).length > 0).map((d) => `${shortDate(d)}: ${dueOn(d).map((e) => `${e.name} ${money(e.amount)}`).join(", ")}.`),
  ].join(" ");
  return (
    <section className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 800 }}>When your bills fall</h2>
        <span style={{ fontSize: 12, color: "var(--ink-2)" }}>The next five weeks, from today. Each bill sits on its due day, as tall as its amount; a blue column is a payday.</span>
      </div>
      <div role="img" aria-label={said} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <div style={{ display: "flex", gap: 2, height: 96, alignItems: "stretch" }}>
          {days.map((d) => {
            const payday = (byDay.get(d) ?? []).some((e) => e.kind === "paycheck");
            const due = dueOn(d);
            return (
              <div key={d} title={[shortDate(d), payday ? "payday" : "", ...due.map((e) => `${e.name} ${money(e.amount)}`)].filter(Boolean).join(" · ")}
                style={{ flex: "1 1 0", minWidth: 0, display: "flex", flexDirection: "column", justifyContent: "flex-end", gap: 1, borderRadius: 3, background: payday ? "var(--paycheck-soft)" : "var(--surface-2)", borderTop: payday ? "4px solid var(--paycheck)" : "4px solid transparent" }}>
                {due.map((e, i) => <div key={`${e.source}-${i}`} style={{ height: `${Math.max(6, (e.amount / top) * 80)}%`, background: "var(--bill)", borderRadius: 2 }} />)}
              </div>
            );
          })}
        </div>
        <div aria-hidden="true" style={{ position: "relative", height: 16, fontSize: 11, color: "var(--muted)" }}>
          {[0, 7, 14, 21, 28].map((i) => (
            <span key={i} className="num" style={{ position: "absolute", left: `${(i / STRIP_DAYS) * 100}%`, whiteSpace: "nowrap" }}>{i === 0 ? "today" : monthDay(days[i])}</span>
          ))}
        </div>
      </div>
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12, color: "var(--ink-2)" }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span className="sw" style={{ background: "var(--paycheck)", margin: 0 }} aria-hidden="true" />Payday: {paydays.length ? paydays.map(shortDate).join(", ") : "none in these five weeks"}</span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span className="sw" style={{ background: "var(--bill)", margin: 0 }} aria-hidden="true" />A bill on its due day ({bills.length} in these five weeks)</span>
      </div>
    </section>
  );
}

// ---- one bill's edits ----------------------------------------------------------------------------------------------

// One bill's edits, shared by its card (phone) and its row (wider screens).
function useBillEditor(b: Bill, onSaved: () => void) {
  const payerOf = (x: Bill) => (x.pay_from_account_id === null ? "" : String(x.pay_from_account_id));
  const [payer, setPayer] = useState(payerOf(b));
  const [amount, setAmount] = useState(String(b.amount));
  const [cadence, setCadence] = useState(b.cadence || "monthly");
  const [dueDay, setDueDay] = useState(String(b.due_day));
  const [anchor, setAnchor] = useState(b.anchor_date ?? "");
  const [autopay, setAutopay] = useState(b.autopay);
  const [movable, setMovable] = useState(b.movable);
  const [paidFrom, setPaidFrom] = useState(b.paid_from ?? "due");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  const saved = [b.amount, b.cadence, b.due_day, b.anchor_date, b.autopay, b.movable, b.paid_from, b.pay_from_account_id].join("|");
  useEffect(() => {
    // What the server holds now is what the fields show, after a save here or anywhere else.
    setPayer(payerOf(b));
    setAmount(String(b.amount));
    setCadence(b.cadence || "monthly");
    setDueDay(String(b.due_day));
    setAnchor(b.anchor_date ?? "");
    setAutopay(b.autopay);
    setMovable(b.movable);
    setPaidFrom(b.paid_from ?? "due");
  }, [saved]); // eslint-disable-line react-hooks/exhaustive-deps
  const monthly = cadence === "monthly";
  const dirty =
    parseMoney(amount) !== b.amount || cadence !== (b.cadence || "monthly") || (monthly ? dueDayOf(dueDay) !== b.due_day : anchor !== (b.anchor_date ?? "")) ||
    autopay !== b.autopay || movable !== b.movable || paidFrom !== (b.paid_from ?? "due") || payer !== payerOf(b);

  async function save() {
    const value = parseMoney(amount);
    if (value === null || value < 0) return setNote({ text: AMOUNT_HINT, tone: "error" });
    const day = monthly ? dueDayOf(dueDay) : anchor ? parseIso(anchor).getDate() : null;
    if (day === null) return setNote({ text: monthly ? DAY_HINT : DATE_HINT, tone: "error" });
    setBusy(true);
    setNote(null);
    try {
      // "checking (not set)" is sent as null, so a bill once put on a card comes off it.
      const body = { amount: value, cadence, due_day: day, ...(monthly ? {} : { anchor_date: anchor }), autopay, movable, paid_from: paidFrom, pay_from_account_id: payer ? Number(payer) : null };
      await api(`/api/v1/bills/${b.id}`, { method: "PUT", body: JSON.stringify(body) });
      setNote({ text: "Saved.", tone: "info" });
      onSaved();
    } catch (e) {
      setNote({ text: (e as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    await api(`/api/v1/bills/${b.id}`, { method: "PUT", body: JSON.stringify({ active: false }) });
    onSaved();
  }

  return { payer, setPayer, amount, setAmount, cadence, setCadence, monthly, dueDay, setDueDay, anchor, setAnchor, autopay, setAutopay, movable, setMovable, paidFrom, setPaidFrom, busy, note, dirty, save, remove };
}

type Editor = ReturnType<typeof useBillEditor>;

function PayerSelect({ b, e, accounts }: { b: Bill; e: Editor; accounts: AccountRow[] }) {
  return (
    <select className="input" value={e.payer} onChange={(x) => e.setPayer(x.target.value)} aria-label={`Which account ${b.name} is charged to`} style={{ width: "100%" }}>
      <option value="">checking (not set)</option>
      {accounts.filter((a) => ["checking", "savings", "cash", "card"].includes(a.kind)).map((a) => <option key={a.id} value={a.id}>{a.kind === "card" ? `${a.name} (card)` : a.name}</option>)}
    </select>
  );
}

function CadenceSelect({ b, e }: { b: Bill; e: Editor }) {
  return (
    <select className="input" value={e.cadence} onChange={(x) => e.setCadence(x.target.value)} aria-label={`How often ${b.name} comes`} style={{ width: "100%" }}>
      {CADENCES.map((c) => <option key={c.value} value={c.value}>{c.words}</option>)}
    </select>
  );
}

/** A monthly bill keeps its day of the month; any other keeps its next date. */
function DueInput({ b, e, labelled }: { b: Bill; e: Editor; labelled?: boolean }) {
  return e.monthly
    ? <input className="input num" style={{ width: labelled ? "100%" : 64 }} value={e.dueDay} onChange={(x) => e.setDueDay(x.target.value)} inputMode="numeric" aria-label={labelled ? undefined : `Due day of ${b.name}, 1 to 31`} />
    : <input className="input" type="date" style={{ width: "100%" }} value={e.anchor} onChange={(x) => e.setAnchor(x.target.value)} aria-label={labelled ? undefined : `Next date ${b.name} is due`} />;
}

const OLD_PAID_FROM: Record<string, string> = { due: "the check before it is due", mid: "the check on the 15th", end: "the check at month end" };

// Which paycheck pays a bill: the engine's own choices for the real pay schedule. A saved choice that no longer fits
// stays listed, marked, so the select never shows something other than what is kept.
function PaidFromSelect({ b, e, choices }: { b: Bill; e: Editor; choices: PaidFromChoice[] }) {
  const all = choices.some((c) => c.value === e.paidFrom) ? choices : [...choices, { value: e.paidFrom, words: `${OLD_PAID_FROM[e.paidFrom] ?? e.paidFrom} (as saved; it does not fit your paydays)` }];
  return (
    <select className="input" value={e.paidFrom} onChange={(x) => e.setPaidFrom(x.target.value)} aria-label={`Which paycheck pays ${b.name}`} style={{ width: "100%" }}>
      {all.map((c) => <option key={c.value} value={c.value}>{c.next ? `${c.words}, next ${shortDate(c.next)}` : c.words}</option>)}
    </select>
  );
}

/** The choices when the server does not list them: the check before it is due, and the 15th and month-end checks
 * only for a pay schedule of twice a month. */
function usePaidFromChoices(data: BillsOut | null): PaidFromChoice[] {
  const given = data?.paid_from_choices;
  const incomes = useApi<{ incomes: { cadence: string }[] }>(data && !given?.length ? "/api/v1/incomes" : null);
  if (given && given.length) return given;
  const twice = (incomes.data?.incomes ?? []).some((i) => i.cadence === "semimonthly");
  return [{ value: "due", words: OLD_PAID_FROM.due }, ...(twice ? [{ value: "mid", words: OLD_PAID_FROM.mid }, { value: "end", words: OLD_PAID_FROM.end }] : [])];
}

function Actions({ b, e }: { b: Bill; e: Editor }) {
  return (
    <span style={{ display: "inline-flex", gap: 6, flexWrap: "wrap", alignItems: "flex-start", justifyContent: "flex-end" }}>
      <button className="btn dark" type="button" aria-label={`Save ${b.name}`} disabled={!e.dirty || e.busy} onClick={e.save}>{e.busy ? "Saving…" : "Save"}</button>
      <ConfirmButton label="Remove" name={`Remove ${b.name}`} question={`Take ${b.name} off your bills list?`} yes="Yes, remove it" no="Keep it" onConfirm={e.remove} />
    </span>
  );
}

function BillTableRow({ b, accounts, choices, onSaved }: { b: Bill; accounts: AccountRow[]; choices: PaidFromChoice[]; onSaved: () => void }) {
  const e = useBillEditor(b, onSaved);
  return (
    <tr>
      <td><strong>{b.name}</strong><div style={{ fontSize: 11, color: "var(--ink-2)" }}>{billLine(b)}</div><div style={{ fontSize: 11, color: "var(--muted)" }}>{b.kind}{b.category ? ` · ${b.category}` : ""}</div></td>
      <td><input className="input num" style={{ width: 96 }} value={e.amount} onChange={(x) => e.setAmount(x.target.value)} inputMode="decimal" aria-label={`Amount of ${b.name}`} /></td>
      <td style={{ minWidth: 150 }}><CadenceSelect b={b} e={e} /></td>
      <td style={{ minWidth: e.monthly ? 0 : 150 }}><DueInput b={b} e={e} /></td>
      <td><PayerSelect b={b} e={e} accounts={accounts} /></td>
      <td><PaidFromSelect b={b} e={e} choices={choices} /></td>
      <td><input type="checkbox" checked={e.autopay} onChange={(x) => e.setAutopay(x.target.checked)} aria-label={`Autopay: ${b.name}`} style={{ width: 18, height: 18 }} /></td>
      <td><input type="checkbox" checked={e.movable} onChange={(x) => e.setMovable(x.target.checked)} aria-label={`Date can move: ${b.name}`} style={{ width: 18, height: 18 }} /></td>
      <td style={{ textAlign: "right" }}>
        <Actions b={b} e={e} />
        {e.note && <div style={{ marginTop: 4 }}><Said text={e.note.text} tone={e.note.tone} /></div>}
      </td>
    </tr>
  );
}

function BillCard({ b, accounts, choices, onSaved }: { b: Bill; accounts: AccountRow[]; choices: PaidFromChoice[]; onSaved: () => void }) {
  const e = useBillEditor(b, onSaved);
  const field = { display: "flex", flexDirection: "column", gap: 4, minWidth: 0 } as const;
  return (
    <li className="card" style={{ padding: 12, display: "flex", flexDirection: "column", gap: 10 }}>
      <div>
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 800 }}>{b.name}</h2>
        <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{billLine(b)}</div>
        <div style={{ fontSize: 12, color: "var(--muted)" }}>{b.kind}{b.category ? ` · ${b.category}` : ""}</div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 8 }}>
        <label style={field}><span className="lab">Amount</span><input className="input num" value={e.amount} onChange={(x) => e.setAmount(x.target.value)} inputMode="decimal" /></label>
        <label style={field}><span className="lab">How often</span><CadenceSelect b={b} e={e} /></label>
      </div>
      <label style={field}><span className="lab">{e.monthly ? "Due day (1 to 31)" : "Next due date"}</span><DueInput b={b} e={e} labelled /></label>
      <label style={field}><span className="lab">Charged to</span><PayerSelect b={b} e={e} accounts={accounts} /></label>
      <label style={field}><span className="lab">Paid from</span><PaidFromSelect b={b} e={e} choices={choices} /></label>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 13 }}>
        <label style={{ display: "inline-flex", gap: 8, alignItems: "center", minHeight: 32 }}><input type="checkbox" checked={e.autopay} onChange={(x) => e.setAutopay(x.target.checked)} style={{ width: 18, height: 18 }} />Autopay</label>
        <label style={{ display: "inline-flex", gap: 8, alignItems: "center", minHeight: 32 }}><input type="checkbox" checked={e.movable} onChange={(x) => e.setMovable(x.target.checked)} style={{ width: 18, height: 18 }} />Date can move</label>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <Actions b={b} e={e} />
        {e.note && <Said text={e.note.text} tone={e.note.tone} />}
      </div>
    </li>
  );
}

// ---- the page ------------------------------------------------------------------------------------------------------

export function Bills({ onChanged }: { onChanged: () => void }) {
  const state = useApi<BillsOut>("/api/v1/bills");
  const { data, reload } = state;
  const { data: accounts } = useApi<{ accounts: AccountRow[] }>("/api/v1/accounts");
  const choices = usePaidFromChoices(data);
  // Bills with no payment seen, to answer here: Today's "Pay X; it was due" opens this page.
  const needs = useApi<{ past_due: PastDueRow[] }>("/api/v1/needs-you", { live: true });
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("");
  const [cadence, setCadence] = useState("monthly");
  const [dueDay, setDueDay] = useState("1");
  const [nextDate, setNextDate] = useState("");
  const [adding, setAdding] = useState(false);
  const [addNote, setAddNote] = useState<Note>(null);
  // Goes up on every save here, so the date mover below asks again (a tick on "Date can move" changes no count).
  const [tick, setTick] = useState(0);
  const total = data?.monthly_total ?? 0; // the engine's, each bill by its own cadence
  const accountRows = accounts?.accounts ?? [];

  function changed() {
    reload();
    setTick((t) => t + 1);
    onChanged();
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    const value = parseMoney(amount);
    if (value === null || value < 0) return setAddNote({ text: AMOUNT_HINT, tone: "error" });
    const monthly = cadence === "monthly";
    const day = monthly ? dueDayOf(dueDay) : nextDate ? parseIso(nextDate).getDate() : null;
    if (day === null) return setAddNote({ text: monthly ? DAY_HINT : DATE_HINT, tone: "error" });
    setAdding(true);
    setAddNote(null);
    try {
      await api("/api/v1/bills", { method: "POST", body: JSON.stringify({ name, amount: value, cadence, due_day: day, ...(monthly ? {} : { anchor_date: nextDate }) }) });
      setAddNote({ text: `${name} is on your bills list: ${money(value, { cents: true })} ${eachWord(cadence)}.`, tone: "info" });
      setName("");
      setAmount("");
      setNextDate("");
      changed();
    } catch (err) {
      setAddNote({ text: (err as Error).message, tone: "error" });
    } finally {
      setAdding(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 1100 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Bills</h1>
        <span style={{ fontSize: 13, color: "var(--ink-2)" }}>{data ? `${data.bills.length} bills and subscriptions · about ${money(total)} a month today, each counted by how often it comes` : ""}</span>
      </div>
      {data && ((data.one_time?.items.length ?? 0) > 0 || (data.starting_later?.items.length ?? 0) > 0) && (
        <div style={{ fontSize: 13, color: "var(--ink-2)", display: "flex", flexDirection: "column", gap: 2 }}>
          {data.one_time && data.one_time.items.length > 0 && (
            <span>Not in the monthly figure, counted once on their day: {data.one_time.items.map((i) => `${i.name} ${money(i.amount, { cents: true })} on ${shortDate(i.date)}`).join("; ")}.</span>
          )}
          {data.starting_later && data.starting_later.items.length > 0 && (
            <span>Starting later, so not in today's figure: {data.starting_later.items.map((i) => `${i.name} ${money(i.per_month, { cents: true })} a month from ${shortDate(i.starts_on)}`).join("; ")}.</span>
          )}
        </div>
      )}
      <section id="paid" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <PaidQuestions state={needs} onAnswered={changed} here="bills" />
      </section>
      <BillStrip />
      <Show
        state={state}
        what="your bills"
        isEmpty={(d) => d.bills.length === 0}
        empty={<Empty>No bills yet. Add one below, <Link to="/settings/connectors#import">import a bills list</Link>, or name repeating charges on <Link to="/confirm">Needs you</Link>.</Empty>}
      >
        {(d) => (
          <>
            {/* The wrapper carries md:hidden: an inline display on the list itself would override it. */}
            <div className="md:hidden">
              <ul aria-label="Your bills" style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 8 }}>
                {d.bills.map((b) => <BillCard key={b.id} b={b} accounts={accountRows} choices={choices} onSaved={changed} />)}
              </ul>
            </div>
            <div className="card hidden md:block" style={{ padding: "6px 14px", overflowX: "auto" }}>
              <table className="ledger">
                <thead><tr><th>Bill</th><th>Amount</th><th>How often</th><th>Due day or next date</th><th>Charged to</th><th>Paid from</th><th>Autopay</th><th>Date can move</th><th><span className="sr-only">Save or remove</span></th></tr></thead>
                <tbody>{d.bills.map((b) => <BillTableRow key={b.id} b={b} accounts={accountRows} choices={choices} onSaved={changed} />)}</tbody>
              </table>
            </div>
          </>
        )}
      </Show>
      <form onSubmit={add} className="card" style={{ padding: 14, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 200px" }}><span className="lab">Add a bill: its name</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} required /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Amount</span><input className="input num" style={{ width: 110 }} value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" required /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="lab">How often</span>
          <select className="input" value={cadence} onChange={(e) => setCadence(e.target.value)}>
            {CADENCES.map((c) => <option key={c.value} value={c.value}>{c.words}</option>)}
          </select>
        </label>
        {cadence === "monthly"
          ? <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Due day (1 to 31)</span><input className="input num" style={{ width: 80 }} value={dueDay} onChange={(e) => setDueDay(e.target.value)} inputMode="numeric" required /></label>
          : <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Next due date</span><input className="input" type="date" value={nextDate} onChange={(e) => setNextDate(e.target.value)} required /></label>}
        <button className="btn dark" type="submit" disabled={adding}>{adding ? "Adding…" : "Add"}</button>
        {addNote && <Said text={addNote.text} tone={addNote.tone} />}
      </form>
      {data && <DateMoves refreshKey={tick} />}
      <PlannedPayments onChanged={onChanged} />
    </div>
  );
}
