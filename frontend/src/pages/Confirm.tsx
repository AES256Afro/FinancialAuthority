// Needs you: recurring charges the engine found and does not yet know what to call. One tap each.
import { useState } from "react";
import { api, useApi } from "../api";
import { money, shortDate } from "../format";
import type { SeriesRow } from "../types";

const CADENCE: Record<string, string> = { weekly: "every week", biweekly: "every two weeks", monthly: "monthly", quarterly: "quarterly", yearly: "yearly" };

function Row({ s, onDone }: { s: SeriesRow; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [autopay, setAutopay] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isIncome = s.kind === "income";

  async function classify(kind: string) {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/v1/recurring/${s.id}/classify`, { method: "POST", body: JSON.stringify({ kind, autopay, remaining: kind === "installment" ? 2 : undefined }) });
      onDone();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <div>
          <strong style={{ fontSize: 15 }}>{s.merchant}</strong>
          <div style={{ fontSize: 12, color: "var(--ink-2)" }}>
            {CADENCE[s.cadence] ?? s.cadence} · {s.charges_seen} charges since {shortDate(s.first_seen)} · last {shortDate(s.last_date)} · next about {shortDate(s.next_expected)}
            {s.category ? ` · ${s.category}` : ""}
          </div>
        </div>
        <strong className="num" style={{ fontSize: 18 }}>{isIncome ? money(s.typical_amount, { sign: true }) : money(s.typical_amount, { cents: true })}</strong>
      </div>
      <div style={{ fontSize: 12, color: "var(--muted)" }}>Found in the ledger. Nothing from mail is involved yet; this is what the transactions say.</div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        {isIncome ? (
          <>
            <button className="btn dark" type="button" disabled={busy} onClick={() => classify("income")}>It's a paycheck</button>
            <button className="btn" type="button" disabled={busy} onClick={() => classify("ignored")}>Not income</button>
          </>
        ) : (
          <>
            <button className="btn dark" type="button" disabled={busy} onClick={() => classify("bill")}>Bill</button>
            <button className="btn" type="button" disabled={busy} onClick={() => classify("subscription")}>Subscription</button>
            <button className="btn" type="button" disabled={busy} onClick={() => classify("installment")}>Installment plan</button>
            <button className="btn" type="button" disabled={busy} onClick={() => classify("ignored")}>Ignore</button>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, marginLeft: "auto" }}>
              <input type="checkbox" checked={autopay} onChange={(e) => setAutopay(e.target.checked)} />
              autopay
            </label>
          </>
        )}
      </div>
      {error && <div style={{ color: "var(--critical)", fontSize: 13 }}>{error}</div>}
    </div>
  );
}

export function Confirm({ onChanged }: { onChanged: () => void }) {
  const { data, reload, loading } = useApi<{ series: SeriesRow[]; pending: number }>("/api/v1/recurring");
  const [detecting, setDetecting] = useState(false);

  async function detect() {
    setDetecting(true);
    try {
      await api("/api/v1/recurring/detect", { method: "POST" });
      reload();
      onChanged();
    } finally {
      setDetecting(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 760 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Needs you{data ? ` · ${data.pending}` : ""}</h1>
        <div style={{ flexGrow: 1 }} />
        <button className="btn" type="button" onClick={detect} disabled={detecting}>Look again</button>
      </div>
      <p style={{ margin: 0, color: "var(--ink-2)", fontSize: 13 }}>Each one becomes a bill, a subscription, a paycheck or an installment plan, and then it is in every view. Confirmations from mail arrive with M4.</p>
      {loading && <div style={{ color: "var(--muted)" }}>Looking…</div>}
      {data && data.series.length === 0 && (
        <div className="card" style={{ padding: 18, color: "var(--ink-2)" }}>Nothing is waiting. Every recurring charge the ledger shows has a name.</div>
      )}
      {data?.series.map((s) => (
        <Row
          key={s.id}
          s={s}
          onDone={() => {
            reload();
            onChanged();
          }}
        />
      ))}
    </div>
  );
}
