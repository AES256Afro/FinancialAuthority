// Bills: every confirmed bill and subscription, editable in place. On a phone each bill is a card; from the md
// breakpoint up they sit in one table. The list comes first; the date mover and planned payments follow it.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { ConfirmButton } from "../components/ConfirmButton";
import { DateMoves } from "../components/DateMoves";
import { PlannedPayments } from "../components/PlannedPayments";
import { Empty, Said, Show } from "../components/State";
import { money, parseMoney } from "../format";
import type { BillRow } from "../types";

interface AccountRow {
  id: number;
  name: string;
  kind: string;
}

type Note = { text: string; tone: "info" | "error" } | null;

const AMOUNT_HINT = "Type an amount like 120 or 1200.50";
const DAY_HINT = "Type a due day from 1 to 31.";

/** A due day typed by a person: a whole number from 1 to 31, else null. */
function dueDayOf(text: string): number | null {
  const clean = text.trim();
  if (!/^\d{1,2}$/.test(clean)) return null;
  const n = Number(clean);
  return n >= 1 && n <= 31 ? n : null;
}

// One bill's edits, shared by its card (phone) and its row (wider screens).
function useBillEditor(b: BillRow, onSaved: () => void) {
  const payerOf = (x: BillRow) => (x.pay_from_account_id === null ? "" : String(x.pay_from_account_id));
  const [payer, setPayer] = useState(payerOf(b));
  const [amount, setAmount] = useState(String(b.amount));
  const [dueDay, setDueDay] = useState(String(b.due_day));
  const [autopay, setAutopay] = useState(b.autopay);
  const [movable, setMovable] = useState(b.movable);
  const [paidFrom, setPaidFrom] = useState(b.paid_from ?? "due");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  const saved = [b.amount, b.due_day, b.autopay, b.movable, b.paid_from, b.pay_from_account_id].join("|");
  useEffect(() => {
    // What the server holds now is what the fields show, after a save here or anywhere else.
    setPayer(payerOf(b));
    setAmount(String(b.amount));
    setDueDay(String(b.due_day));
    setAutopay(b.autopay);
    setMovable(b.movable);
    setPaidFrom(b.paid_from ?? "due");
  }, [saved]); // eslint-disable-line react-hooks/exhaustive-deps
  const dirty = parseMoney(amount) !== b.amount || dueDayOf(dueDay) !== b.due_day || autopay !== b.autopay || movable !== b.movable || paidFrom !== (b.paid_from ?? "due") || payer !== payerOf(b);

  async function save() {
    const value = parseMoney(amount);
    const day = dueDayOf(dueDay);
    if (value === null || value < 0) return setNote({ text: AMOUNT_HINT, tone: "error" });
    if (day === null) return setNote({ text: DAY_HINT, tone: "error" });
    setBusy(true);
    setNote(null);
    try {
      await api(`/api/v1/bills/${b.id}`, { method: "PUT", body: JSON.stringify({ amount: value, due_day: day, autopay, movable, paid_from: paidFrom, pay_from_account_id: payer ? Number(payer) : undefined }) });
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

  return { payer, setPayer, amount, setAmount, dueDay, setDueDay, autopay, setAutopay, movable, setMovable, paidFrom, setPaidFrom, busy, note, dirty, save, remove };
}

type Editor = ReturnType<typeof useBillEditor>;

function PayerSelect({ b, e, accounts }: { b: BillRow; e: Editor; accounts: AccountRow[] }) {
  return (
    <select className="input" value={e.payer} onChange={(x) => e.setPayer(x.target.value)} aria-label={`Which account ${b.name} is charged to`} style={{ width: "100%" }}>
      <option value="">checking (not set)</option>
      {accounts.filter((a) => ["checking", "savings", "cash", "card"].includes(a.kind)).map((a) => <option key={a.id} value={a.id}>{a.kind === "card" ? `${a.name} (card)` : a.name}</option>)}
    </select>
  );
}

// The engine's paid_from is due, mid or end; mid and end are its two fixed paydays of a month.
function PaidFromSelect({ b, e }: { b: BillRow; e: Editor }) {
  return (
    <select className="input" value={e.paidFrom} onChange={(x) => e.setPaidFrom(x.target.value)} aria-label={`Which paycheck pays ${b.name}`} style={{ width: "100%" }}>
      <option value="due">the check before it is due</option>
      <option value="mid">the 15th check</option>
      <option value="end">the end-of-month check</option>
    </select>
  );
}

function Actions({ b, e }: { b: BillRow; e: Editor }) {
  return (
    <span style={{ display: "inline-flex", gap: 6, flexWrap: "wrap", alignItems: "flex-start", justifyContent: "flex-end" }}>
      <button className="btn dark" type="button" disabled={!e.dirty || e.busy} onClick={e.save}>{e.busy ? "Saving…" : "Save"}</button>
      <ConfirmButton label="Remove" question={`Take ${b.name} off your bills list?`} yes="Yes, remove it" no="Keep it" onConfirm={e.remove} />
    </span>
  );
}

function BillTableRow({ b, accounts, onSaved }: { b: BillRow; accounts: AccountRow[]; onSaved: () => void }) {
  const e = useBillEditor(b, onSaved);
  return (
    <tr>
      <td><strong>{b.name}</strong><div style={{ fontSize: 11, color: "var(--muted)" }}>{b.kind}{b.category ? ` · ${b.category}` : ""}</div></td>
      <td><input className="input num" style={{ width: 96 }} value={e.amount} onChange={(x) => e.setAmount(x.target.value)} inputMode="decimal" aria-label={`Amount of ${b.name}`} /></td>
      <td><input className="input num" style={{ width: 64 }} value={e.dueDay} onChange={(x) => e.setDueDay(x.target.value)} inputMode="numeric" aria-label={`Due day of ${b.name}, 1 to 31`} /></td>
      <td><PayerSelect b={b} e={e} accounts={accounts} /></td>
      <td><PaidFromSelect b={b} e={e} /></td>
      <td><input type="checkbox" checked={e.autopay} onChange={(x) => e.setAutopay(x.target.checked)} aria-label={`Autopay: ${b.name}`} style={{ width: 18, height: 18 }} /></td>
      <td><input type="checkbox" checked={e.movable} onChange={(x) => e.setMovable(x.target.checked)} aria-label={`Date can move: ${b.name}`} style={{ width: 18, height: 18 }} /></td>
      <td style={{ textAlign: "right" }}>
        <Actions b={b} e={e} />
        {e.note && <div style={{ marginTop: 4 }}><Said text={e.note.text} tone={e.note.tone} /></div>}
      </td>
    </tr>
  );
}

function BillCard({ b, accounts, onSaved }: { b: BillRow; accounts: AccountRow[]; onSaved: () => void }) {
  const e = useBillEditor(b, onSaved);
  const field = { display: "flex", flexDirection: "column", gap: 4, minWidth: 0 } as const;
  return (
    <li className="card" style={{ padding: 12, display: "flex", flexDirection: "column", gap: 10 }}>
      <div>
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 800 }}>{b.name}</h2>
        <div style={{ fontSize: 12, color: "var(--muted)" }}>{b.kind}{b.category ? ` · ${b.category}` : ""}</div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 8 }}>
        <label style={field}><span className="lab">Amount</span><input className="input num" value={e.amount} onChange={(x) => e.setAmount(x.target.value)} inputMode="decimal" /></label>
        <label style={field}><span className="lab">Due day (1 to 31)</span><input className="input num" value={e.dueDay} onChange={(x) => e.setDueDay(x.target.value)} inputMode="numeric" /></label>
      </div>
      <label style={field}><span className="lab">Charged to</span><PayerSelect b={b} e={e} accounts={accounts} /></label>
      <label style={field}><span className="lab">Paid from</span><PaidFromSelect b={b} e={e} /></label>
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

export function Bills({ onChanged }: { onChanged: () => void }) {
  const state = useApi<{ bills: BillRow[]; monthly_total: number; monthly_basis: string }>("/api/v1/bills");
  const { data, reload } = state;
  const { data: accounts } = useApi<{ accounts: AccountRow[] }>("/api/v1/accounts");
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("");
  const [dueDay, setDueDay] = useState("1");
  const [adding, setAdding] = useState(false);
  const [addNote, setAddNote] = useState<Note>(null);
  const total = data?.monthly_total ?? 0; // the engine's, each bill by its own cadence
  const accountRows = accounts?.accounts ?? [];

  function changed() {
    reload();
    onChanged();
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    const value = parseMoney(amount);
    const day = dueDayOf(dueDay);
    if (value === null || value < 0) return setAddNote({ text: AMOUNT_HINT, tone: "error" });
    if (day === null) return setAddNote({ text: DAY_HINT, tone: "error" });
    setAdding(true);
    setAddNote(null);
    try {
      await api("/api/v1/bills", { method: "POST", body: JSON.stringify({ name, amount: value, due_day: day }) });
      setAddNote({ text: `${name} is on your bills list.`, tone: "info" });
      setName("");
      setAmount("");
      changed();
    } catch (err) {
      setAddNote({ text: (err as Error).message, tone: "error" });
    } finally {
      setAdding(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 1000 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Bills</h1>
        <span style={{ fontSize: 13, color: "var(--ink-2)" }}>{data ? `${data.bills.length} bills and subscriptions · about ${money(total)} a month` : ""}</span>
      </div>
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
                {d.bills.map((b) => <BillCard key={b.id} b={b} accounts={accountRows} onSaved={changed} />)}
              </ul>
            </div>
            <div className="card hidden md:block" style={{ padding: "6px 14px", overflowX: "auto" }}>
              <table className="ledger">
                <thead><tr><th>Bill</th><th>Amount</th><th>Due day</th><th>Charged to</th><th>Paid from</th><th>Autopay</th><th>Date can move</th><th><span className="sr-only">Save or remove</span></th></tr></thead>
                <tbody>{d.bills.map((b) => <BillTableRow key={b.id} b={b} accounts={accountRows} onSaved={changed} />)}</tbody>
              </table>
            </div>
          </>
        )}
      </Show>
      <form onSubmit={add} className="card" style={{ padding: 14, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 200px" }}><span className="lab">Add a bill: its name</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} required /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Amount</span><input className="input num" style={{ width: 110 }} value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" required /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Due day (1 to 31)</span><input className="input num" style={{ width: 80 }} value={dueDay} onChange={(e) => setDueDay(e.target.value)} inputMode="numeric" required /></label>
        <button className="btn dark" type="submit" disabled={adding}>{adding ? "Adding…" : "Add"}</button>
        {addNote && <Said text={addNote.text} tone={addNote.tone} />}
      </form>
      {data && <DateMoves refreshKey={data.bills.length} />}
      <PlannedPayments onChanged={onChanged} />
    </div>
  );
}
