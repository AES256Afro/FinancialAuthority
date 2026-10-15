// Bills: every confirmed bill and subscription, editable in place.
import { useState } from "react";
import { api, useApi } from "../api";
import { DateMoves } from "../components/DateMoves";
import { PlannedPayments } from "../components/PlannedPayments";
import { money } from "../format";
import type { BillRow } from "../types";

interface AccountRow {
  id: number;
  name: string;
  kind: string;
}

function Editor({ b, accounts, onSaved }: { b: BillRow; accounts: AccountRow[]; onSaved: () => void }) {
  const [payer, setPayer] = useState(b.pay_from_account_id === null ? "" : String(b.pay_from_account_id));
  const [amount, setAmount] = useState(String(b.amount));
  const [dueDay, setDueDay] = useState(String(b.due_day));
  const [autopay, setAutopay] = useState(b.autopay);
  const [movable, setMovable] = useState(b.movable);
  const [paidFrom, setPaidFrom] = useState(b.paid_from ?? "due");
  const [busy, setBusy] = useState(false);
  const dirty = Number(amount) !== b.amount || Number(dueDay) !== b.due_day || autopay !== b.autopay || movable !== b.movable || paidFrom !== (b.paid_from ?? "due") || payer !== (b.pay_from_account_id === null ? "" : String(b.pay_from_account_id));

  async function save() {
    setBusy(true);
    try {
      await api(`/api/v1/bills/${b.id}`, { method: "PUT", body: JSON.stringify({ amount: Number(amount), due_day: Number(dueDay), autopay, movable, paid_from: paidFrom, pay_from_account_id: payer ? Number(payer) : undefined }) });
      onSaved();
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    await api(`/api/v1/bills/${b.id}`, { method: "PUT", body: JSON.stringify({ active: false }) });
    onSaved();
  }

  return (
    <tr>
      <td><strong>{b.name}</strong><div style={{ fontSize: 11, color: "var(--muted)" }}>{b.kind}{b.category ? ` · ${b.category}` : ""}</div></td>
      <td><input className="input num" style={{ width: 96 }} value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" aria-label={`Amount of ${b.name}`} /></td>
      <td><input className="input num" style={{ width: 64 }} value={dueDay} onChange={(e) => setDueDay(e.target.value)} inputMode="numeric" aria-label={`Due day of ${b.name}`} /></td>
      <td>
        <select className="input" value={payer} onChange={(e) => setPayer(e.target.value)} aria-label={`Which account ${b.name} is charged to`}>
          <option value="">checking (not set)</option>
          {accounts.filter((a) => ["checking", "savings", "cash", "card"].includes(a.kind)).map((a) => <option key={a.id} value={a.id}>{a.kind === "card" ? `${a.name} (card)` : a.name}</option>)}
        </select>
      </td>
      <td>
        <select className="input" value={paidFrom} onChange={(e) => setPaidFrom(e.target.value)} aria-label={`Which paycheck pays ${b.name}`}>
          <option value="due">the check before it is due</option>
          <option value="mid">the 15th check</option>
          <option value="end">the end-of-month check</option>
        </select>
      </td>
      <td><label style={{ display: "inline-flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={autopay} onChange={(e) => setAutopay(e.target.checked)} />autopay</label></td>
      <td><label style={{ display: "inline-flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={movable} onChange={(e) => setMovable(e.target.checked)} />can move</label></td>
      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
        <button className="btn dark" type="button" disabled={!dirty || busy} onClick={save}>Save</button>{" "}
        <button className="btn" type="button" onClick={remove} aria-label={`Remove ${b.name}`}>Remove</button>
      </td>
    </tr>
  );
}

export function Bills({ onChanged }: { onChanged: () => void }) {
  const { data, reload } = useApi<{ bills: BillRow[] }>("/api/v1/bills");
  const { data: accounts } = useApi<{ accounts: AccountRow[] }>("/api/v1/accounts");
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("");
  const [dueDay, setDueDay] = useState("1");
  const total = (data?.bills ?? []).reduce((sum, b) => sum + (b.cadence === "monthly" ? b.amount : b.cadence === "semimonthly" ? b.amount * 2 : b.cadence === "biweekly" ? b.amount * 2.17 : b.cadence === "weekly" ? b.amount * 4.33 : b.cadence === "yearly" ? b.amount / 12 : b.amount / 3), 0);

  function changed() {
    reload();
    onChanged();
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    await api("/api/v1/bills", { method: "POST", body: JSON.stringify({ name, amount: Number(amount), due_day: Number(dueDay) }) });
    setName("");
    setAmount("");
    changed();
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 1000 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Bills</h1>
        <span style={{ fontSize: 13, color: "var(--ink-2)" }}>{data ? `${data.bills.length} bills and subscriptions · about ${money(total)} a month` : ""}</span>
      </div>
      <DateMoves refreshKey={data?.bills.length} />
      <PlannedPayments onChanged={onChanged} />
      <div className="card" style={{ padding: "6px 14px", overflowX: "auto" }}>
        <table className="ledger">
          <thead><tr><th>Bill</th><th>Amount</th><th>Due day</th><th>Charged to</th><th>Paid from</th><th></th><th></th><th></th></tr></thead>
          <tbody>{(data?.bills ?? []).map((b) => <Editor key={`${b.id}-${b.amount}-${b.due_day}-${b.autopay}-${b.movable}-${b.paid_from}-${b.pay_from_account_id}`} b={b} accounts={accounts?.accounts ?? []} onSaved={changed} />)}</tbody>
        </table>
      </div>
      <form onSubmit={add} className="card" style={{ padding: 14, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 200px" }}><span className="lab">Add a bill</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" required /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Amount</span><input className="input num" style={{ width: 110 }} value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" required /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Due day</span><input className="input num" style={{ width: 80 }} value={dueDay} onChange={(e) => setDueDay(e.target.value)} inputMode="numeric" required /></label>
        <button className="btn dark" type="submit">Add</button>
      </form>
      <div style={{ fontSize: 12, color: "var(--muted)" }}>Payments, grace days and late fees come with M2.5; the date mover with M5.5.</div>
    </div>
  );
}
