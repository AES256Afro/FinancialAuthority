// Needs you: recurring charges the engine found and does not yet know what to call. One tap each.
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { Approvals } from "../components/Approvals";
import { ConfirmButton } from "../components/ConfirmButton";
import { Empty, Failed, Loading, Said } from "../components/State";
import { money, parseMoney, shortDate, when } from "../format";
import type { FactRow, SeriesRow } from "../types";

const FACT_LABEL: Record<string, string> = { export: "Rocket Money export, ready to import", bill_due: "Bill due", statement: "Statement", paystub: "Paystub", installment: "Installment plan", advance: "Advance", price_change: "Price change", past_due: "Past-due notice" };

function FactCard({ f, onDone }: { f: FactRow; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [amount, setAmount] = useState(f.fields.amount !== undefined ? String(f.fields.amount) : "");
  const [due, setDue] = useState(f.fields.due_date ? String(f.fields.due_date) : "");
  const [error, setError] = useState<string | null>(null);
  const sender = f.message?.sender_name || String(f.fields.sender_name || "");
  const who = String(f.fields.name || "") || sender;
  const via = f.fields.name && sender && String(f.fields.name) !== sender ? sender : "";

  async function confirm() {
    const typed = amount.trim() ? parseMoney(amount) : null;
    if (amount.trim() && typed === null) {
      setError("Type an amount like 120 or 1200.50");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api(`/api/v1/facts/${f.id}/confirm`, { method: "POST", body: JSON.stringify({ apply: true, amount: typed ?? undefined, due_date: due || undefined }) });
      onDone();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }
  async function discard() {
    await api(`/api/v1/facts/${f.id}/reject`, { method: "POST" });
    onDone();
  }

  return (
    <div className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 8, borderColor: f.kind === "past_due" ? "var(--critical)" : undefined }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <div>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>{FACT_LABEL[f.kind] ?? f.kind}{who ? ` · ${who}` : ""}</h3>
          <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{f.message?.subject}{f.message?.date ? ` · ${when(f.message.date)}` : ""} · from mail{via ? ` via ${via}` : ""} · {Math.round(f.confidence * 100)}% sure</div>
        </div>
        {f.fields.amount !== undefined && <strong className="num" style={{ fontSize: 18 }}>{money(Number(f.fields.amount), { cents: true })}</strong>}
      </div>
      {f.kind === "export" ? (
        <div style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.45 }}>
          Importing downloads the file from <strong style={{ color: "var(--ink)" }}>{String(f.fields.host ?? "the link in the email")}</strong> and adds its transactions; rows already here are not added twice, and the import can be undone on Settings › Backups. Import only if you asked Rocket Money for an export and the sender above is Rocket Money.
        </div>
      ) : (
        <div style={{ background: "var(--surface-2)", borderRadius: 10, padding: "10px 12px", fontSize: 12, color: "var(--ink-2)", lineHeight: 1.45 }}>
          <span className="lab" style={{ display: "block", marginBottom: 4 }}>Quoted from the email. Anything it asks the app to do is ignored.</span>“{f.excerpt}”
        </div>
      )}
      {(f.kind === "bill_due" || f.kind === "past_due" || f.kind === "price_change") && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Amount</span><input className="input num" style={{ width: 120 }} value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Due date</span><input className="input" type="date" value={due} onChange={(e) => setDue(e.target.value)} /></label>
        </div>
      )}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button className="btn dark" type="button" disabled={busy} onClick={confirm}>{f.kind === "export" ? (busy ? "Importing…" : "Import it") : busy ? "Confirming…" : "Confirm"}</button>
        <ConfirmButton label="Wrong, discard" question="Discard what was read from this email?" yes="Yes, discard it" no="Keep it" onConfirm={discard} />
        {f.message && <Link to={`/mail?message=${f.message.id}`} style={{ fontSize: 13, marginLeft: "auto" }}>Open the message</Link>}
      </div>
      <Said text={error} tone="error" />
    </div>
  );
}

const CADENCE: Record<string, string> = { weekly: "every week", biweekly: "every two weeks", semimonthly: "twice a month", monthly: "monthly", quarterly: "quarterly", yearly: "yearly" };

function Row({ s, onDone }: { s: SeriesRow; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [autopay, setAutopay] = useState(false);
  const [remaining, setRemaining] = useState("");
  const [error, setError] = useState<string | null>(null);
  const isIncome = s.kind === "income";
  const hint = s.suggested_kind && s.suggested_kind !== "income" ? s.suggested_kind : "";
  const HINT: Record<string, string> = { bill: "Looks like a bill", subscription: "Looks like a subscription", installment: "Looks like a pay-later plan", ignored: "Looks like regular spending, not a bill" };
  // The guess is said in the button's own words, not only by its darker colour.
  const mark = (kind: string) => (hint === kind ? " (suggested)" : "");

  async function classify(kind: string) {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/v1/recurring/${s.id}/classify`, { method: "POST", body: JSON.stringify({ kind, autopay, remaining: kind === "installment" ? Math.max(1, Number(remaining) || 1) : undefined }) });
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
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>{s.merchant}</h3>
          <div style={{ fontSize: 12, color: "var(--ink-2)" }}>
            {CADENCE[s.cadence] ?? s.cadence} · {s.charges_seen} charges since {shortDate(s.first_seen)} · last {shortDate(s.last_date)} · next about {shortDate(s.next_expected)}
            {s.category ? ` · ${s.category}` : ""}
          </div>
        </div>
        <strong className="num" style={{ fontSize: 18 }}>{isIncome ? money(s.typical_amount, { sign: true }) : money(s.typical_amount, { cents: true })}</strong>
      </div>
      <div style={{ fontSize: 12, color: "var(--muted)" }}>Found in your transactions. {hint && HINT[hint] ? `${HINT[hint]}${s.category ? ` (${s.category})` : ""}; the button marked (suggested) is that guess. ` : ""}Nothing from mail is involved yet; this is what the transactions say.</div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
        {isIncome ? (
          <>
            <button className="btn dark" type="button" disabled={busy} onClick={() => classify("income")}>It's a paycheck</button>
            <button className="btn" type="button" disabled={busy} onClick={() => classify("ignored")}>Not income</button>
          </>
        ) : (
          <>
            <button className={hint === "subscription" || hint === "ignored" || hint === "installment" ? "btn" : "btn dark"} type="button" disabled={busy} onClick={() => classify("bill")}>Bill{mark("bill")}</button>
            <button className={hint === "subscription" ? "btn dark" : "btn"} type="button" disabled={busy} onClick={() => classify("subscription")}>Subscription{mark("subscription")}</button>
            <span style={{ display: "inline-flex", gap: 6, alignItems: "flex-end" }}>
              <button className={hint === "installment" ? "btn dark" : "btn"} type="button" disabled={busy} onClick={() => classify("installment")}>Installment plan{mark("installment")}</button>
              <label style={{ display: "inline-flex", flexDirection: "column", gap: 2 }} title="How many payments are left; the lender's app shows it">
                <span className="lab">Payments left</span>
                <input className="input num" style={{ width: 70 }} value={remaining} onChange={(e) => setRemaining(e.target.value)} inputMode="numeric" />
              </label>
            </span>
            <button className={hint === "ignored" ? "btn dark" : "btn"} type="button" disabled={busy} onClick={() => classify("ignored")}>Ignore{mark("ignored")}</button>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, marginLeft: "auto", minHeight: 40 }}>
              <input type="checkbox" checked={autopay} onChange={(e) => setAutopay(e.target.checked)} style={{ width: 18, height: 18 }} />
              Paid automatically (autopay)
            </label>
          </>
        )}
      </div>
      <Said text={error} tone="error" />
    </div>
  );
}

export function Confirm({ onChanged }: { onChanged: () => void }) {
  const { data, reload, error } = useApi<{ series: SeriesRow[]; pending: number }>("/api/v1/recurring");
  const facts = useApi<{ facts: FactRow[]; pending: number }>("/api/v1/facts?status=pending");
  const [detecting, setDetecting] = useState(false);
  const [looked, setLooked] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  const total = (data?.pending ?? 0) + (facts.data?.pending ?? 0);

  async function detect() {
    setDetecting(true);
    setLooked(null);
    try {
      const r = await api<{ created: number; linked: number }>("/api/v1/recurring/detect", { method: "POST" });
      const found = [r.created ? `${r.created} new repeating charge${r.created === 1 ? "" : "s"}` : "", r.linked ? `${r.linked} matched to your bills` : ""].filter(Boolean).join(", ");
      setLooked({ tone: "info", text: found ? `Looked again: ${found}.` : "Looked again. Nothing new found." });
      reload();
      facts.reload();
      onChanged();
    } catch (e) {
      setLooked({ tone: "error", text: `Couldn't look again: ${(e as Error).message}` });
    } finally {
      setDetecting(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 760 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Needs you{data && facts.data ? ` · ${total}` : ""}</h1>
        <div style={{ flexGrow: 1 }} />
        <Said text={looked?.text ?? null} tone={looked?.tone} />
        <button className="btn" type="button" onClick={detect} disabled={detecting}>{detecting ? "Looking…" : "Look again"}</button>
      </div>
      <p style={{ margin: 0, color: "var(--ink-2)", fontSize: 13 }}>First: emails waiting for your OK. Then: details read from your mail. Then: repeating charges found in your transactions. Once you answer one, every screen uses it.</p>
      <Approvals onChanged={onChanged} />
      {facts.error && !facts.data && <Failed error={facts.error} what="what was read from your mail" onRetry={facts.reload} />}
      {facts.data && facts.data.facts.length > 0 && (
        <>
          <h2 className="lab" style={{ margin: 0 }}>From the mail · {facts.data.pending}</h2>
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
        </>
      )}
      {error && !data && <Failed error={error} what="the repeating charges" onRetry={reload} />}
      {!data && !error && <Loading what="what needs you" />}
      {data && data.series.length === 0 && facts.data && facts.data.facts.length === 0 && (
        <Empty to="/today" action="Back to Today">Nothing needs you. Every repeating charge in your transactions has a name, and the mail has nothing new.</Empty>
      )}
      {data && data.series.length > 0 && <h2 className="lab" style={{ margin: "6px 0 0" }}>From your transactions · {data.pending}</h2>}
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
