// Should I buy this? An amount in, a verdict out: yes, wait or no, the first thing it breaks, the first date
// it would pass. The engine decides; this card asks and shows.
import { CircleCheck, CircleHelp, OctagonAlert, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { api } from "../api";
import { money, parseMoney, shortDate } from "../format";
import { Said } from "./State";

interface Check {
  amount: number;
  on: string;
  installments: number | null;
  verdict: "yes" | "wait" | "no" | "outside";
  outside?: { horizon: string; charges: { date: string; amount: number }[] } | null;
  breaks: { date: string; name: string; event_date: string; shortfall: number; already_short: number } | null;
  first_ok_date: string | null;
  low_point: { date: string; at_pace: number };
  fits_safe_to_spend: boolean;
  safe_to_spend: { amount: number; through: string };
  not_advice: string;
  caveats?: string[];
}

// The verdict is a word and an icon on a soft fill, never colour alone.
const VERDICT = {
  yes: { word: "Yes", bg: "var(--safe-soft)", ink: "var(--safe-ink)", Icon: CircleCheck },
  wait: { word: "Wait", bg: "var(--warning-soft)", ink: "var(--warning-ink)", Icon: TriangleAlert },
  no: { word: "No", bg: "var(--critical-soft)", ink: "var(--critical-ink)", Icon: OctagonAlert },
  outside: { word: "Can't tell yet", bg: "var(--paycheck-soft)", ink: "var(--paycheck-ink)", Icon: CircleHelp },
};

export function PurchaseCheck() {
  const [amount, setAmount] = useState("");
  const [split, setSplit] = useState(false);
  const [result, setResult] = useState<Check | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(e: React.FormEvent) {
    e.preventDefault();
    const value = parseMoney(amount);
    if (value === null || value < 0) {
      setError("Type an amount like 120 or 1200.50");
      return;
    }
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
  const v = r ? VERDICT[r.verdict] : null;
  return (
    <section className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
      <h2 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>Should I buy this?</h2>
      <form onSubmit={run} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 3, flex: "1 1 120px", maxWidth: 180 }}>
          <span className="lab">How much it costs</span>
          <input className="input num" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="120" />
        </label>
        <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, minHeight: 40 }}>
          <input type="checkbox" checked={split} onChange={(e) => setSplit(e.target.checked)} /> Split into 4 payments (pay later)
        </label>
        <button className="btn dark" type="submit" disabled={busy || amount === ""}>{busy ? "Checking…" : "Check"}</button>
      </form>
      {error && <Said text={error} tone="error" />}
      {r?.caveats && r.caveats.length > 0 && <div style={{ fontSize: 12, color: "var(--warning-ink)", fontWeight: 600 }}>{r.caveats.join(" ")}</div>}
      {r && v && (
        <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
          <div style={{ minWidth: 72, padding: "8px 10px", borderRadius: 12, background: v.bg, color: v.ink, display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6, fontWeight: 800, fontSize: 18 }}>
            <v.Icon size={18} strokeWidth={2.4} aria-hidden="true" />{v.word}
          </div>
          <div style={{ fontSize: 13, lineHeight: 1.5, display: "flex", flexDirection: "column", gap: 2 }}>
            <div>
              <strong>{money(r.amount)}</strong>{r.installments ? ` in ${r.installments} payments` : ""}{r.verdict === "yes" ? ` fits. Your lowest balance would be ${money(r.low_point.at_pace)} on ${shortDate(r.low_point.date)}.` : ""}
            </div>
            {r.breaks && (
              <div>
                {r.breaks.already_short > 0
                  ? `You're already ${money(r.breaks.already_short)} short on ${shortDate(r.breaks.date)}; this would make it ${money(r.breaks.shortfall)}.`
                  : r.breaks.event_date === r.breaks.date
                    ? `There wouldn't be enough for ${r.breaks.name} on ${shortDate(r.breaks.date)}: ${money(r.breaks.shortfall)} short.`
                    : `You would be ${money(r.breaks.shortfall)} short on ${shortDate(r.breaks.date)}, before ${r.breaks.name} on ${shortDate(r.breaks.event_date)}.`}
              </div>
            )}
            {r.verdict === "wait" && r.first_ok_date && <div>It fits from <strong>{shortDate(r.first_ok_date)}</strong>.</div>}
            {r.verdict === "no" && <div>It doesn't fit on any day in the next 60 days.</div>}
            {r.verdict === "outside" && r.outside && (
              <div>
                Up to {shortDate(r.outside.horizon)} it fits, but {r.outside.charges.length === 1 ? "a payment falls" : `${r.outside.charges.length} payments fall`} after that ({r.outside.charges.map((c) => `${money(c.amount)} on ${shortDate(c.date)}`).join(", ")}), past what the plan can see yet. Check again closer to then.
              </div>
            )}
            <div style={{ color: "var(--muted)", fontSize: 12 }}>Safe to spend today is {money(r.safe_to_spend.amount)} through {shortDate(r.safe_to_spend.through)}. {r.not_advice}</div>
          </div>
        </div>
      )}
    </section>
  );
}
