// Should I buy this? An amount in, a verdict out: yes, wait or no, the first thing it breaks, the first date
// it would pass. The engine decides; this card asks and shows.
import { useState } from "react";
import { api } from "../api";
import { money, shortDate } from "../format";

interface Check {
  amount: number;
  on: string;
  installments: number | null;
  verdict: "yes" | "wait" | "no";
  breaks: { date: string; name: string; event_date: string; shortfall: number; already_short: number } | null;
  first_ok_date: string | null;
  low_point: { date: string; at_pace: number };
  fits_safe_to_spend: boolean;
  safe_to_spend: { amount: number; through: string };
  not_advice: string;
}

const TONE = { yes: "var(--safe)", wait: "var(--warning)", no: "var(--critical)" };
const WORD = { yes: "Yes", wait: "Wait", no: "No" };

export function PurchaseCheck() {
  const [amount, setAmount] = useState("");
  const [split, setSplit] = useState(false);
  const [result, setResult] = useState<Check | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(e: React.FormEvent) {
    e.preventDefault();
    const value = Number(amount);
    if (!(value >= 0)) return;
    setBusy(true);
    setError(null);
    try {
      const params = new URLSearchParams({ amount: String(value) });
      if (split) params.set("installments", "4");
      setResult(await api<Check>(`/api/v1/check?${params}`));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const r = result;
  return (
    <section className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
      <strong style={{ fontSize: 14 }}>Should I buy this?</strong>
      <form onSubmit={run} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <input className="input num" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="$ amount" aria-label="Amount" style={{ flex: "1 1 120px", maxWidth: 180 }} />
        <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13 }}>
          <input type="checkbox" checked={split} onChange={(e) => setSplit(e.target.checked)} /> pay in four
        </label>
        <button className="btn dark" type="submit" disabled={busy || amount === ""}>Check</button>
      </form>
      {error && <div style={{ fontSize: 13, color: "var(--critical)" }}>{error}</div>}
      {r && (
        <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
          <div style={{ minWidth: 64, padding: "8px 0", borderRadius: 12, background: TONE[r.verdict], color: "#fff", textAlign: "center", fontWeight: 800, fontSize: 18 }}>{WORD[r.verdict]}</div>
          <div style={{ fontSize: 13, lineHeight: 1.5, display: "flex", flexDirection: "column", gap: 2 }}>
            <div>
              <strong>{money(r.amount)}</strong>{r.installments ? ` in ${r.installments} installments` : ""}{r.verdict === "yes" ? ` fits. The low point stays ${money(r.low_point.at_pace)} on ${shortDate(r.low_point.date)}.` : ""}
            </div>
            {r.breaks && (
              <div>
                {r.breaks.already_short > 0
                  ? `The honest line is already ${money(r.breaks.already_short)} short on ${shortDate(r.breaks.date)}; this would make it ${money(r.breaks.shortfall)}, with ${r.breaks.name} due ${r.breaks.event_date === r.breaks.date ? "that day" : shortDate(r.breaks.event_date)}.`
                  : r.breaks.event_date === r.breaks.date
                    ? `It breaks ${r.breaks.name} on ${shortDate(r.breaks.date)} by ${money(r.breaks.shortfall)}.`
                    : `It leaves you ${money(r.breaks.shortfall)} short on ${shortDate(r.breaks.date)}, before ${r.breaks.name} on ${shortDate(r.breaks.event_date)}.`}
              </div>
            )}
            {r.verdict === "wait" && r.first_ok_date && <div>It passes from <strong>{shortDate(r.first_ok_date)}</strong>.</div>}
            {r.verdict === "no" && <div>No date in the next sixty days passes.</div>}
            <div style={{ color: "var(--muted)", fontSize: 12 }}>Safe to spend today is {money(r.safe_to_spend.amount)} through {shortDate(r.safe_to_spend.through)}. {r.not_advice}</div>
          </div>
        </div>
      )}
    </section>
  );
}
