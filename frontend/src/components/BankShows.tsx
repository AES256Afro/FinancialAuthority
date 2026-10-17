// What the bank's own screen shows, typed in: a bank's feed can run a day behind and leave holds out, so the owner can
// read the balance off the bank app and the plan uses it until the bank sends something newer (at most a day).
import { Landmark } from "lucide-react";
import { useState } from "react";
import { api } from "../api";
import { money, parseMoney, when } from "../format";
import type { Snapshot } from "../types";
import { Said } from "./State";
import { slipCheck, type Slip } from "../slip";

const LINKED = new Set(["plaid", "teller", "demo"]);  // demo: the sample household shows the control too

export function BankShows({ s }: { s: Snapshot }) {
  const accounts = (s.fresh?.accounts ?? []).filter((a) => LINKED.has(a.connector));
  const told = accounts.filter((a) => a.told);
  const [open, setOpen] = useState(false);
  const [pick, setPick] = useState<number | null>(null);
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  // A figure far above what the app holds waits for a second look (the usual slip is a dropped decimal point).
  const [slip, setSlip] = useState<Slip | null>(null);
  if (accounts.length === 0) return null;
  const chosen = accounts.find((a) => a.id === pick) ?? accounts.find((a) => a.kind === "checking") ?? accounts[0];
  const reads = parseMoney(amount);

  async function use(confirmed?: number) {
    const value = confirmed ?? parseMoney(amount);
    if (value === null) {
      setSaid({ text: "Type the amount your bank shows, like 215.80", tone: "error" });
      return;
    }
    if (confirmed === undefined) {
      const check = slipCheck(amount, value, chosen.balance);
      if (check) {
        setSlip(check);
        return;
      }
    }
    setSlip(null);
    setBusy(true);
    setSaid(null);
    try {
      const r = await api<{ said: string }>(`/api/v1/banks/accounts/${chosen.id}/shows`, { method: "POST", body: JSON.stringify({ amount: value }) });
      setSaid({ text: r.said, tone: "info" });
      setAmount("");
      setOpen(false);
    } catch (e) {
      setSaid({ text: (e as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function clear(id: number) {
    setBusy(true);
    setSaid(null);
    try {
      const r = await api<{ said: string }>(`/api/v1/banks/accounts/${id}/shows`, { method: "DELETE" });
      setSaid({ text: r.said, tone: "info" });
    } catch (e) {
      setSaid({ text: (e as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12, color: "var(--ink-2)" }}>
      {told.map((a) => (
        <div key={a.id} style={{ display: "flex", alignItems: "flex-start", gap: 6 }}>
          <Landmark size={13} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2 }} />
          <span>
            {a.name}: <span className="num">{money(a.told!.amount, { cents: true })}</span>{a.kind === "card" ? " owed" : ""}, as you read it off your bank {when(a.told!.at)}. It counts until the bank sends newer data.{" "}
            <button type="button" className="btn" disabled={busy} onClick={() => clear(a.id)} style={{ minHeight: 24, padding: "0 8px", fontSize: 11 }}
              aria-label={`Clear the balance you typed for ${a.name}${a.told!.bank !== null && a.told!.bank !== undefined ? ` and go back to the bank's ${money(a.told!.bank, { cents: true })}` : ""}`}>
              Clear
            </button>
          </span>
        </div>
      ))}
      {!open ? (
        <div>
          <button type="button" className="btn" onClick={() => { setOpen(true); setSaid(null); }} style={{ minHeight: 28, padding: "2px 10px", fontSize: 12 }}>
            Your bank shows a different balance?
          </button>{" "}
          <Said text={said?.text ?? null} tone={said?.tone} />
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, border: "1px solid var(--hairline)", borderRadius: 10, padding: 10 }}>
          <span>
            Banks send their data a few times a day, and some leave out money on hold. Open your bank's app and type what it shows now:
            for checking, the <strong>available</strong> balance; for a card, what you owe. The plan uses it until the bank sends something newer, at most a day.
          </span>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <span className="lab">Account</span>
              <select className="input" value={chosen.id} onChange={(e) => { setPick(Number(e.target.value)); setSlip(null); }} style={{ minHeight: 32 }}>
                {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </label>
            <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <span className="lab">{chosen.kind === "card" ? "Owed" : "Available"}</span>
              <input className="input" inputMode="decimal" value={amount} onChange={(e) => { setAmount(e.target.value); setSaid(null); setSlip(null); }} onKeyDown={(e) => { if (e.key === "Enter") use(); }} placeholder="0.00" style={{ width: 110, minHeight: 32 }} aria-label={`What your bank shows for ${chosen.name}`} />
            </label>
            {!slip && <button type="button" className="btn dark" disabled={busy} onClick={() => use()} style={{ minHeight: 32 }}>Use this</button>}
            <button type="button" className="btn" disabled={busy} onClick={() => { setOpen(false); setSaid(null); setSlip(null); }} style={{ minHeight: 32 }}>Cancel</button>
          </div>
          {reads !== null && !slip && <span>Reads as <strong className="num">{money(reads, { cents: true })}</strong>.</span>}
          {slip && (
            <div role="alert" style={{ display: "flex", flexDirection: "column", gap: 6, borderTop: "1px solid var(--hairline)", paddingTop: 6 }}>
              <span style={{ color: "var(--ink)" }}>
                That reads as <strong className="num">{money(slip.value, { cents: true })}</strong>, far more than the{" "}
                {chosen.balance !== null && chosen.balance !== undefined ? <span className="num">{money(chosen.balance, { cents: true })}</span> : "nothing"} the app has for {chosen.name}. Which is it?
              </span>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {slip.maybe !== null && <button type="button" className="btn dark" disabled={busy} onClick={() => { setAmount(slip.maybe!.toFixed(2)); use(slip.maybe!); }} style={{ minHeight: 32 }}>Use {money(slip.maybe, { cents: true })}</button>}
                <button type="button" className="btn" disabled={busy} onClick={() => use(slip.value)} style={{ minHeight: 32 }}>Yes, {money(slip.value, { cents: true })}</button>
                <button type="button" className="btn" disabled={busy} onClick={() => setSlip(null)} style={{ minHeight: 32 }}>Let me fix it</button>
              </div>
            </div>
          )}
          <Said text={said?.text ?? null} tone={said?.tone} />
        </div>
      )}
    </div>
  );
}
