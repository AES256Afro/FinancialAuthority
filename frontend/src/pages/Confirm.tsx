// Needs you: recurring charges the engine found and does not yet know what to call. One tap each.
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { Approvals } from "../components/Approvals";
import { money, shortDate } from "../format";
import type { FactRow, SeriesRow } from "../types";

const FACT_LABEL: Record<string, string> = { bill_due: "Bill due", statement: "Statement", paystub: "Paystub", installment: "Installment plan", advance: "Advance", price_change: "Price change", past_due: "Past-due notice" };

function FactCard({ f, onDone }: { f: FactRow; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [amount, setAmount] = useState(f.fields.amount !== undefined ? String(f.fields.amount) : "");
  const [due, setDue] = useState(f.fields.due_date ? String(f.fields.due_date) : "");
  const [error, setError] = useState<string | null>(null);
  const sender = f.message?.sender_name || String(f.fields.sender_name || "");
  const who = String(f.fields.name || "") || sender;
  const via = f.fields.name && sender && String(f.fields.name) !== sender ? sender : "";

  async function act(path: "confirm" | "reject") {
    setBusy(true);
    setError(null);
    try {
      const body = path === "confirm" ? JSON.stringify({ apply: true, amount: amount ? Number(amount) : undefined, due_date: due || undefined }) : undefined;
      await api(`/api/v1/facts/${f.id}/${path}`, { method: "POST", body });
      onDone();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 8, borderColor: f.kind === "past_due" ? "var(--critical)" : undefined }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <div>
          <strong style={{ fontSize: 15 }}>{FACT_LABEL[f.kind] ?? f.kind}{who ? ` · ${who}` : ""}</strong>
          <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{f.message?.subject}{f.message?.date ? ` · ${shortDate(f.message.date.slice(0, 10))}` : ""} · from mail{via ? ` via ${via}` : ""} · {Math.round(f.confidence * 100)}% sure</div>
        </div>
        {f.fields.amount !== undefined && <strong className="num" style={{ fontSize: 18 }}>{money(Number(f.fields.amount), { cents: true })}</strong>}
      </div>
      <div style={{ background: "var(--surface-2)", borderRadius: 10, padding: "10px 12px", fontSize: 12, color: "var(--ink-2)", lineHeight: 1.45 }}>
        <span className="lab" style={{ display: "block", marginBottom: 4 }}>Quoted from the message, treated as data</span>“{f.excerpt}”
      </div>
      {(f.kind === "bill_due" || f.kind === "past_due" || f.kind === "price_change") && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Amount</span><input className="input num" style={{ width: 120 }} value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Due date</span><input className="input" type="date" value={due} onChange={(e) => setDue(e.target.value)} /></label>
        </div>
      )}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button className="btn dark" type="button" disabled={busy} onClick={() => act("confirm")}>Confirm</button>
        <button className="btn" type="button" disabled={busy} onClick={() => act("reject")}>Not this</button>
        {f.message && <Link to="/mail" style={{ fontSize: 13, marginLeft: "auto" }}>Open the message</Link>}
      </div>
      {error && <div style={{ color: "var(--critical)", fontSize: 13 }}>{error}</div>}
    </div>
  );
}

const CADENCE: Record<string, string> = { weekly: "every week", biweekly: "every two weeks", monthly: "monthly", quarterly: "quarterly", yearly: "yearly" };

function Row({ s, onDone }: { s: SeriesRow; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [autopay, setAutopay] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isIncome = s.kind === "income";
  const hint = s.suggested_kind && s.suggested_kind !== "income" ? s.suggested_kind : "";
  const HINT: Record<string, string> = { bill: "Looks like a bill", subscription: "Looks like a subscription", ignored: "Looks like regular spending, not a bill" };

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
      <div style={{ fontSize: 12, color: "var(--muted)" }}>Found in the ledger. {hint ? `${HINT[hint]}${s.category ? ` (${s.category})` : ""}; the dark button is that guess. ` : ""}Nothing from mail is involved yet; this is what the transactions say.</div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        {isIncome ? (
          <>
            <button className="btn dark" type="button" disabled={busy} onClick={() => classify("income")}>It's a paycheck</button>
            <button className="btn" type="button" disabled={busy} onClick={() => classify("ignored")}>Not income</button>
          </>
        ) : (
          <>
            <button className={hint === "subscription" || hint === "ignored" ? "btn" : "btn dark"} type="button" disabled={busy} onClick={() => classify("bill")}>Bill</button>
            <button className={hint === "subscription" ? "btn dark" : "btn"} type="button" disabled={busy} onClick={() => classify("subscription")}>Subscription</button>
            <button className="btn" type="button" disabled={busy} onClick={() => classify("installment")}>Installment plan</button>
            <button className={hint === "ignored" ? "btn dark" : "btn"} type="button" disabled={busy} onClick={() => classify("ignored")}>Ignore</button>
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
  const facts = useApi<{ facts: FactRow[]; pending: number }>("/api/v1/facts?status=pending");
  const [detecting, setDetecting] = useState(false);
  const total = (data?.pending ?? 0) + (facts.data?.pending ?? 0);

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
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Needs you{data && facts.data ? ` · ${total}` : ""}</h1>
        <div style={{ flexGrow: 1 }} />
        <button className="btn" type="button" onClick={detect} disabled={detecting}>Look again</button>
      </div>
      <p style={{ margin: 0, color: "var(--ink-2)", fontSize: 13 }}>Sends waiting for your tap first, then facts from the mail, then recurring charges from the ledger. Each one, once named, is in every view.</p>
      <Approvals onChanged={onChanged} />
      {facts.data && facts.data.facts.length > 0 && (
        <>
          <div className="lab">From the mail · {facts.data.pending}</div>
          {facts.data.facts.map((f) => (
            <FactCard
              key={f.id}
              f={f}
              onDone={() => {
                facts.reload();
                onChanged();
              }}
            />
          ))}
          <div className="lab" style={{ marginTop: 6 }}>From the ledger · {data?.pending ?? 0}</div>
        </>
      )}
      {loading && <div style={{ color: "var(--muted)" }}>Looking…</div>}
      {data && data.series.length === 0 && (facts.data?.facts.length ?? 0) === 0 && (
        <div className="card" style={{ padding: 18, color: "var(--ink-2)" }}>Nothing is waiting. Every recurring charge the ledger shows has a name, and the mail has nothing new.</div>
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
