// Planned payments: what the owner says is leaving before it shows up in the transactions. Counted from its date,
// netted off the card it goes to, and settled by itself when the transaction shows up.
import { useState } from "react";
import { api, useApi } from "../api";
import { localToday, money, parseMoney, shortDate } from "../format";
import { ConfirmButton } from "./ConfirmButton";
import { Failed, Loading, Said } from "./State";

interface PlannedRow {
  id: number;
  name: string;
  amount: number;
  date: string;
  kind: string;
  to_account_id: number | null;
  done: boolean;
  transaction_id: number | null;
}

type Note = { text: string; tone: "info" | "error" } | null;

const KIND_WORD: Record<string, string> = { card_payment: "card payment", bill: "bill", transfer: "transfer", other: "payment" };

export function PlannedPayments({ onChanged }: { onChanged?: () => void }) {
  const state = useApi<{ items: PlannedRow[]; cards: { id: number; name: string; balance: number | null }[] }>("/api/v1/planned");
  const { data, reload } = state;
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("");
  const [date, setDate] = useState(localToday());
  const [kind, setKind] = useState("card_payment");
  const [card, setCard] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  const [rowNote, setRowNote] = useState<{ id: number; text: string } | null>(null);

  function changed() {
    reload();
    onChanged?.();
  }
  async function add(e: React.FormEvent) {
    e.preventDefault();
    const value = parseMoney(amount);
    if (value === null || value <= 0) {
      setNote({ text: "Type an amount like 120 or 1200.50", tone: "error" });
      return;
    }
    setBusy(true);
    setNote(null);
    try {
      const target = kind === "card_payment" && card ? Number(card) : undefined;
      const label = name.trim() || (target ? `${data?.cards.find((c) => c.id === target)?.name ?? "Card"} payment` : KIND_WORD[kind]);
      await api("/api/v1/planned", { method: "POST", body: JSON.stringify({ name: label, amount: value, date, kind, to_account_id: target }) });
      setNote({ text: `Counted: ${label} on ${shortDate(date)}.`, tone: "info" });
      setName("");
      setAmount("");
      changed();
    } catch (err) {
      setNote({ text: (err as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  }
  async function wentThrough(id: number) {
    setRowNote(null);
    try {
      await api(`/api/v1/planned/${id}/done`, { method: "POST" });
      changed();
    } catch (e) {
      setRowNote({ id, text: (e as Error).message });
    }
  }
  async function remove(id: number) {
    await api(`/api/v1/planned/${id}`, { method: "DELETE" });
    changed();
  }
  const open = (data?.items ?? []).filter((p) => !p.done);
  const settled = (data?.items ?? []).filter((p) => p.done).slice(0, 4);
  return (
    <section className="card" id="planned" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 800 }}>Planned payments</h2>
        <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Payments you've sent that haven't shown up yet. They count now and clear by themselves when they show up.</span>
      </div>
      {state.error && !data && <Failed error={state.error} what="planned payments" onRetry={reload} card={false} />}
      {!data && !state.error && <Loading what="planned payments" card={false} />}
      {open.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table className="ledger" style={{ fontSize: 13 }}>
            <thead><tr><th>Payment</th><th style={{ textAlign: "right" }}>Amount</th><th><span className="sr-only">Went through or remove</span></th></tr></thead>
            <tbody>
              {open.map((p) => (
                <tr key={p.id}>
                  <td><strong>{p.name}</strong><div style={{ fontSize: 11, color: "var(--muted)" }}>{KIND_WORD[p.kind] ?? p.kind} · leaves {shortDate(p.date)}</div></td>
                  <td className="num" style={{ textAlign: "right", whiteSpace: "nowrap" }}>{money(p.amount, { cents: true })}</td>
                  <td style={{ textAlign: "right" }}>
                    <span style={{ display: "inline-flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end", alignItems: "flex-start" }}>
                      <button className="btn" type="button" onClick={() => wentThrough(p.id)}>It went through</button>
                      <ConfirmButton label="Remove" name={`Remove ${p.name}`} question={`Stop counting ${p.name}?`} yes="Yes, remove it" no="Keep it" onConfirm={() => remove(p.id)} />
                    </span>
                    {rowNote?.id === p.id && <div style={{ marginTop: 4 }}><Said text={rowNote.text} tone="error" /></div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <form onSubmit={add} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">What</span>
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="card_payment">card payment</option>
            <option value="bill">a bill</option>
            <option value="transfer">a transfer</option>
            <option value="other">something else</option>
          </select>
        </label>
        {kind === "card_payment" && (data?.cards.length ?? 0) > 0 && (
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">To</span>
            <select className="input" value={card} onChange={(e) => { setCard(e.target.value); const c = data?.cards.find((x) => String(x.id) === e.target.value); if (c?.balance && !amount) setAmount(String(c.balance)); }}>
              <option value="">a card</option>
              {data?.cards.map((c) => <option key={c.id} value={c.id}>{c.name}{c.balance !== null ? ` (${money(c.balance, { cents: true })} owed)` : ""}</option>)}
            </select>
          </label>
        )}
        <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 160px" }}><span className="lab">Name (optional)</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Amount</span><input className="input num" style={{ width: 110 }} value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" required /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Leaves on</span><input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} required /></label>
        <button className="btn dark" type="submit" disabled={busy || !amount}>{busy ? "Counting…" : "Count it"}</button>
        {note && <Said text={note.text} tone={note.tone} />}
      </form>
      {settled.length > 0 && <div className="num" style={{ fontSize: 12, color: "var(--ink-2)" }}>Went through: {settled.map((p) => `${p.name} ${money(p.amount)} on ${shortDate(p.date)}`).join(" · ")}</div>}
    </section>
  );
}
