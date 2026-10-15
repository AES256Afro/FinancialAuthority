// Planned payments: what the owner says is leaving before the ledger sees it. Counted from its date, netted off
// the card it goes to, and settled by itself when the transaction posts.
import { useState } from "react";
import { api, useApi } from "../api";
import { money, shortDate } from "../format";

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

const KIND_WORD: Record<string, string> = { card_payment: "card payment", bill: "bill", transfer: "transfer", other: "payment" };

export function PlannedPayments({ onChanged }: { onChanged?: () => void }) {
  const { data, reload } = useApi<{ items: PlannedRow[]; cards: { id: number; name: string; balance: number | null }[] }>("/api/v1/planned");
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("");
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [kind, setKind] = useState("card_payment");
  const [card, setCard] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function changed() {
    reload();
    onChanged?.();
  }
  async function add(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const target = kind === "card_payment" && card ? Number(card) : undefined;
      const label = name.trim() || (target ? `${data?.cards.find((c) => c.id === target)?.name ?? "Card"} payment` : KIND_WORD[kind]);
      await api("/api/v1/planned", { method: "POST", body: JSON.stringify({ name: label, amount: Number(amount), date, kind, to_account_id: target }) });
      setName("");
      setAmount("");
      changed();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function act(id: number, what: "done" | "remove") {
    await api(`/api/v1/planned/${id}${what === "done" ? "/done" : ""}`, { method: what === "done" ? "POST" : "DELETE" });
    changed();
  }
  const open = (data?.items ?? []).filter((p) => !p.done);
  const settled = (data?.items ?? []).filter((p) => p.done).slice(0, 4);
  return (
    <section className="card" id="planned" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <strong style={{ fontSize: 14 }}>Planned payments</strong>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>money you have told the bank to send that the ledger has not seen yet; counted now, cleared by itself when it posts</span>
      </div>
      {open.length > 0 && (
        <table className="ledger" style={{ fontSize: 13 }}>
          <tbody>
            {open.map((p) => (
              <tr key={p.id}>
                <td><strong>{p.name}</strong><div style={{ fontSize: 11, color: "var(--muted)" }}>{KIND_WORD[p.kind] ?? p.kind} · leaves {shortDate(p.date)}</div></td>
                <td className="num" style={{ textAlign: "right" }}>{money(p.amount, { cents: true })}</td>
                <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                  <button className="btn" type="button" onClick={() => act(p.id, "done")}>Posted</button>{" "}
                  <button className="btn" type="button" onClick={() => act(p.id, "remove")}>Remove</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
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
        <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 160px" }}><span className="lab">Name</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="optional" /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Amount</span><input className="input num" style={{ width: 110 }} value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" required /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Leaves on</span><input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} required /></label>
        <button className="btn dark" type="submit" disabled={busy || !amount}>Count it</button>
      </form>
      {error && <div style={{ fontSize: 13, color: "var(--critical)" }}>{error}</div>}
      {settled.length > 0 && <div style={{ fontSize: 12, color: "var(--muted)" }}>Posted: {settled.map((p) => `${p.name} ${money(p.amount)} on ${shortDate(p.date)}`).join(" · ")}</div>}
    </section>
  );
}
