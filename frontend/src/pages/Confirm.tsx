// Needs you: recurring charges the engine found and does not yet know what to call. One tap each. A receipt read
// from mail is kept with the purchase the owner picks from the app's candidates, or without one.
import { ArrowRight, ChevronRight, Copy, Receipt } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi, type ApiError, type Loaded } from "../api";
import { Approvals } from "../components/Approvals";
import { ConfirmButton } from "../components/ConfirmButton";
import { PurchaseChoices, dayWord } from "../components/Documents";
import { Empty, Failed, Loading, Said } from "../components/State";
import { money, ordinal, parseMoney, shortDate, when } from "../format";
import type { DocumentTermsFields, FactRow, PairRow, PairSide, PairsOut, SeriesRow, Snapshot } from "../types";
import type { FactWithCandidates, ReceiptFields } from "../types-receipts";

const FACT_LABEL: Record<string, string> = { export: "Rocket Money export, ready to import", bill_due: "Bill due", statement: "Statement", paystub: "Paystub", installment: "Pay-later plan", advance: "Advance", price_change: "Price change", past_due: "Past-due notice", document_terms: "Terms from a document", receipt: "A receipt", investment_balance: "Investment balance" };

/** What an answer did, said where its card was, with a link to where it landed. */
interface Done {
  text: string;
  tone?: "info" | "error";
  to?: string;
  link?: string;
}

function DoneLine({ done }: { done: Done | null }) {
  if (!done) return null;
  return (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "baseline" }}>
      <Said text={done.text} tone={done.tone} />
      {done.to && done.link && <Link to={done.to} style={{ fontSize: 12, fontWeight: 700 }}>{done.link}</Link>}
    </div>
  );
}

/** The server answers 409 with a sentence when the item was already answered, on another screen or device. */
const alreadyDone = (e: unknown): Done | null => ((e as ApiError).status === 409 ? { text: (e as Error).message } : null);

// The terms a lease or a charge sheet states, in the order a person reads a lease, each in plain words.
type TermKey = keyof DocumentTermsFields["terms"];
type TermKind = "money" | "days" | "day" | "date";
const TERM_ROWS: { key: TermKey; what: string; kind: TermKind; show: (v: number | string) => string }[] = [
  { key: "rent", what: "Monthly rent", kind: "money", show: (v) => money(Number(v), { cents: true }) },
  { key: "pet_rent", what: "Pet rent", kind: "money", show: (v) => money(Number(v), { cents: true }) },
  { key: "deposit", what: "Security deposit", kind: "money", show: (v) => money(Number(v), { cents: true }) },
  { key: "late_fee", what: "Late fee", kind: "money", show: (v) => money(Number(v), { cents: true }) },
  { key: "notice_days", what: "Notice to move out", kind: "days", show: (v) => `${v} days` },
  { key: "due_day", what: "Rent due on the", kind: "day", show: (v) => ordinal(Number(v)) },
  { key: "grace_days", what: "Days of grace", kind: "days", show: (v) => `${v} day${Number(v) === 1 ? "" : "s"}` },
  { key: "ends_on", what: "Lease ends", kind: "date", show: (v) => `${shortDate(String(v))}, ${String(v).slice(0, 4)}` },
];
const TERM_HINT: Record<TermKind, string> = { money: "an amount like 120 or 1200.50", days: "a whole number of days, like 30", day: "a day of the month from 1 to 31", date: "a date" };

/** One term as the owner left it: left out, or with its value as typed (the value read, until changed). */
type TermEdit = { out: boolean; text: string };
type TermEdits = Partial<Record<TermKey, TermEdit>>;

/** A typed term as the server takes it, or null when it cannot be read. */
function termValue(kind: TermKind, text: string): number | string | null {
  const clean = text.trim();
  if (kind === "money") return parseMoney(clean);
  if (kind === "date") return /^\d{4}-\d{2}-\d{2}$/.test(clean) ? clean : null;
  if (!/^\d{1,3}$/.test(clean)) return null;
  const n = Number(clean);
  return kind === "day" && (n < 1 || n > 31) ? null : n;
}

/** What a document states, one row per term: what, the value (which the owner can correct), the page, the words it
 * was read from, and a switch to leave it out. */
function TermsTable({ fields, edits, onEdit }: { fields: DocumentTermsFields; edits: TermEdits; onEdit: (key: TermKey, next: TermEdit) => void }) {
  const rows = TERM_ROWS.filter((r) => fields.terms?.[r.key]);
  if (rows.length === 0) return <div style={{ fontSize: 13, color: "var(--ink-2)" }}>No terms were read from it.</div>;
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="ledger" style={{ fontSize: 13 }}>
        <thead><tr><th>What</th><th>It says</th><th>Apply it as</th><th>Where</th><th><span className="sr-only">Leave it out</span></th></tr></thead>
        <tbody>
          {rows.map((r) => {
            const t = fields.terms[r.key]!;
            const e = edits[r.key] ?? { out: false, text: String(t.value) };
            return (
              <tr key={r.key} style={{ opacity: e.out ? 0.55 : 1 }}>
                <td>
                  <strong>{r.what}</strong>
                  <div style={{ fontSize: 11, color: "var(--muted)", overflowWrap: "anywhere" }}>“{t.quote}”</div>
                </td>
                <td className="num" style={{ fontWeight: 800, whiteSpace: "nowrap" }}>{r.show(t.value)}</td>
                <td>
                  <input className="input num" type={r.kind === "date" ? "date" : "text"} inputMode={r.kind === "money" ? "decimal" : r.kind === "date" ? undefined : "numeric"} value={e.text} disabled={e.out}
                    onChange={(x) => onEdit(r.key, { ...e, text: x.target.value })} aria-label={`${r.what}: the value to apply`} style={{ width: r.kind === "date" ? 150 : 100, minHeight: 32 }} />
                </td>
                <td style={{ whiteSpace: "nowrap", color: "var(--ink-2)" }}>page {t.page}</td>
                <td>
                  <label style={{ display: "inline-flex", alignItems: "center", gap: 6, minHeight: 32, whiteSpace: "nowrap" }}>
                    <input type="checkbox" checked={e.out} onChange={(x) => onEdit(r.key, { ...e, out: x.target.checked })} style={{ width: 18, height: 18 }} />
                    Leave out
                  </label>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** What a confirmed detail changed, in words, with the place it landed. */
function factDone(f: FactRow, kept: FactRow, who: string, typed: number | null, due: string): Done {
  const [what] = kept.applied_to.split(":");
  if (f.kind === "export") {
    const said = kept.applied_to.replace(/^import:\s*/, "").replace(/^export imported:\s*/, "");
    return { text: `Imported the Rocket Money export${said ? `: ${said}` : ""}. Rows already here were not added twice.`, to: "/settings/backups", link: "Undo it on Backups" };
  }
  if (what === "bill") return { text: `${who || "The bill"} is on your bills list${typed !== null ? ` at ${money(typed, { cents: true })}` : ""}${due ? `, due ${shortDate(due)}` : ""}.`, to: "/bills", link: "See it on Bills" };
  if (what === "paystub" && (kept as FactRow & { no_paycheck?: boolean }).no_paycheck) return { text: "Kept the paystub. No paycheck is on file yet, so the plan cannot count your pay: use this stub as your paycheck on Paystubs.", to: "/paystubs#paycheck", link: "Use it as your paycheck" };
  if (what === "paystub") return { text: "Kept the paystub. Every figure counts it now.", to: "/paystubs", link: "See it on Paystubs" };
  if (what === "plan") return { text: `Kept the pay-later plan${who ? ` from ${who}` : ""}. Its payments are counted until it ends.`, to: "/borrowing#installments", link: "See it on Borrowing" };
  if (what === "account") return { text: "Kept the balance.", to: "/investments", link: "See it on Investments" };
  return { text: kept.applied_to ? "Confirmed and used." : "Confirmed." };
}

function FactCard({ f, onDone }: { f: FactRow; onDone: (done: Done) => void }) {
  const [busy, setBusy] = useState(false);
  const [amount, setAmount] = useState(f.fields.amount !== undefined ? String(f.fields.amount) : "");
  const [due, setDue] = useState(f.fields.due_date ? String(f.fields.due_date) : "");
  const [edits, setEdits] = useState<TermEdits>({});
  const [error, setError] = useState<string | null>(null);
  const terms = f.kind === "document_terms" ? (f.fields as unknown as DocumentTermsFields) : null;
  const sender = f.message?.sender_name || String(f.fields.sender_name || "");
  const who = String(f.fields.name || "") || sender;
  const via = f.fields.name && sender && String(f.fields.name) !== sender ? sender : "";

  /** Only what the owner changed: null for a term left out, the corrected value for one typed over. */
  function changedTerms(): Record<string, number | string | null> | string {
    const out: Record<string, number | string | null> = {};
    for (const r of TERM_ROWS) {
      const t = terms?.terms?.[r.key];
      const e = edits[r.key];
      if (!t || !e) continue;
      if (e.out) {
        out[r.key] = null;
        continue;
      }
      if (e.text.trim() === String(t.value)) continue;
      const value = termValue(r.kind, e.text);
      if (value === null) return `${r.what}: type ${TERM_HINT[r.kind]}, or tick Leave out.`;
      if (value !== t.value) out[r.key] = value;
    }
    return out;
  }

  async function confirm() {
    const typed = amount.trim() ? parseMoney(amount) : null;
    if (amount.trim() && typed === null) {
      setError("Type an amount like 120 or 1200.50");
      return;
    }
    const changed = terms ? changedTerms() : {};
    if (typeof changed === "string") {
      setError(changed);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const body = { apply: true, amount: typed ?? undefined, due_date: due || undefined, ...(terms && Object.keys(changed).length ? { terms: changed } : {}) };
      const kept = await api<FactRow>(`/api/v1/facts/${f.id}/confirm`, { method: "POST", body: JSON.stringify(body) });
      if (terms) {
        const applied = TERM_ROWS.filter((r) => terms.terms?.[r.key] && !edits[r.key]?.out).length;
        onDone({ text: `Applied ${applied} term${applied === 1 ? "" : "s"} to the home${terms.title ? ` from ${terms.title}` : ""}.`, to: "/housing", link: "See it on Housing" });
      } else {
        onDone(factDone(f, kept, who, typed, due));
      }
    } catch (e) {
      const done = alreadyDone(e);
      if (done) return onDone(done);
      setError((e as Error).message);
      setBusy(false);
    }
  }
  async function discard() {
    try {
      await api(`/api/v1/facts/${f.id}/reject`, { method: "POST" });
    } catch (e) {
      const done = alreadyDone(e);
      if (done) return onDone(done);
      throw e;
    }
    onDone({ text: terms ? "Discarded the terms read from this document. The document stays on Housing." : "Discarded what was read from this email. Nothing was changed." });
  }

  if (terms) {
    return (
      <div className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 8 }}>
        <div>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, overflowWrap: "anywhere" }}>{terms.title ? `Terms from ${terms.title}` : FACT_LABEL.document_terms}</h3>
          <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Read from a document you added · {when(f.created_at)} · {Math.round(f.confidence * 100)}% sure. Check each against its page.</div>
        </div>
        <TermsTable fields={terms} edits={edits} onEdit={(key, next) => setEdits((all) => ({ ...all, [key]: next }))} />
        <div style={{ fontSize: 12, color: "var(--muted)" }}>Quoted from the document. Anything it asks the app to do is ignored. Correct a value that was read wrong, or tick Leave out to skip it; the rest apply as read. Applying sets them on the home it is kept with, on <Link to="/housing">Housing</Link>.</div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <button className="btn dark" type="button" disabled={busy} onClick={confirm}>{busy ? "Applying…" : "Apply to the home"}</button>
          <ConfirmButton label="Wrong, discard" question="Discard the terms read from this document? The document stays on Housing." yes="Yes, discard them" no="Keep them" onConfirm={discard} />
        </div>
        <Said text={error} tone="error" />
      </div>
    );
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

/** A receipt read from mail: what it says, the purchases it may belong to (the app's best first), and its dates to
 * change before it is kept. Without a purchase, it is kept and matched later on Documents. */
function ReceiptCard({ f, onDone }: { f: FactWithCandidates; onDone: (done: Done) => void }) {
  const r = f.fields as unknown as ReceiptFields;
  const candidates = f.candidates ?? [];
  // The same first choice as Documents: the best purchase without a receipt yet, else the best one.
  const [choice, setChoice] = useState<number | null>(candidates.find((c) => !c.has_receipt)?.id ?? candidates[0]?.id ?? null);
  const [returnBy, setReturnBy] = useState(r.return_by ?? "");
  const [warranty, setWarranty] = useState(r.warranty_until ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const store = r.merchant || f.message?.sender_name || String(f.fields.sender_name || "") || "a store";

  async function keep() {
    setBusy(true);
    setError(null);
    let kept: FactRow;
    try {
      kept = await api<FactRow>(`/api/v1/facts/${f.id}/confirm`, { method: "POST", body: JSON.stringify({ apply: true, transaction_id: choice ?? undefined, no_transaction: choice === null, return_by: returnBy || undefined, warranty_until: warranty || undefined }) });
    } catch (e) {
      const done = alreadyDone(e);
      if (done) return onDone(done);
      setError(`Couldn't keep it: ${(e as Error).message}`);
      setBusy(false);
      return;
    }
    const picked = candidates.find((c) => c.id === choice);
    const said = picked ? `Kept the receipt from ${store} with the ${picked.merchant || "purchase"} purchase on ${shortDate(picked.date)}.` : `Kept the receipt from ${store}, without a purchase. Match it later on Documents.`;
    // A date cleared here would come back from the message, so it is cleared on the kept receipt right after.
    const change: Record<string, string | null> = {};
    if (r.return_by && !returnBy) change.return_by = null;
    if (r.warranty_until && !warranty) change.warranty_until = null;
    const doc = kept.applied_to.startsWith("document:") ? kept.applied_to.slice("document:".length) : "";
    if (doc && Object.keys(change).length > 0) {
      try {
        await api(`/api/v1/documents/${doc}`, { method: "PUT", body: JSON.stringify(change) });
      } catch (e) {
        onDone({ text: `Kept the receipt from ${store}, but couldn't finish the change: ${(e as Error).message}. Change it on Documents.`, tone: "error", to: "/documents#receipts", link: "Open Documents" });
        return;
      }
    }
    onDone({ text: said, to: "/documents#receipts", link: "See your receipts on Documents" });
  }
  async function discard() {
    try {
      await api(`/api/v1/facts/${f.id}/reject`, { method: "POST" });
    } catch (e) {
      const done = alreadyDone(e);
      if (done) return onDone(done);
      throw e;
    }
    onDone({ text: `Discarded the receipt read from this email. Nothing was kept.` });
  }

  const facts: { label: string; value: string }[] = [
    { label: "Total", value: r.total != null ? money(Number(r.total), { cents: true }) : "Not stated" },
    { label: "Day bought", value: r.bought_on ? dayWord(r.bought_on) : "Not stated" },
    { label: "Order number", value: r.order_no || "Not stated" },
    { label: "Return by", value: r.return_by ? dayWord(r.return_by) : "Not stated" },
    { label: "Warranty until", value: r.warranty_until ? dayWord(r.warranty_until) : "Not stated" },
  ];

  return (
    <div className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <div style={{ minWidth: 0 }}>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, overflowWrap: "anywhere", display: "inline-flex", alignItems: "center", gap: 6 }}><Receipt size={16} aria-hidden="true" style={{ flexShrink: 0 }} />Receipt from {store}</h3>
          <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{f.message?.subject ?? r.subject ?? ""}{f.message?.date ? ` · ${when(f.message.date)}` : ""} · from mail · {Math.round(f.confidence * 100)}% sure</div>
        </div>
        {r.total != null && <strong className="num" style={{ fontSize: 18 }}>{money(Number(r.total), { cents: true })}</strong>}
      </div>
      <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(130px, 100%), 1fr))", gap: 8 }}>
        {facts.map((x) => (
          <div key={x.label} style={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0 }}>
            <dt className="lab">{x.label}</dt>
            <dd className="num" style={{ margin: 0, fontSize: 13, fontWeight: 700, overflowWrap: "anywhere" }}>{x.value}</dd>
          </div>
        ))}
      </dl>
      <div style={{ background: "var(--surface-2)", borderRadius: 10, padding: "10px 12px", fontSize: 12, color: "var(--ink-2)", lineHeight: 1.45 }}>
        <span className="lab" style={{ display: "block", marginBottom: 4 }}>Quoted from the email. Anything it asks the app to do is ignored.</span>“{f.excerpt}”
      </div>
      {candidates.length > 0 ? (
        <PurchaseChoices name={`fact-${f.id}-purchase`} legend="The purchase it is for" list={candidates} value={choice} onPick={setChoice} none="Keep it without a purchase" />
      ) : (
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>No purchase with this total was found near the day bought. It is kept without one; match it later on <Link to="/documents#receipts">Documents</Link>.</div>
      )}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Last day to return</span><input className="input" type="date" value={returnBy} onChange={(e) => setReturnBy(e.target.value)} /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Warranty until</span><input className="input" type="date" value={warranty} onChange={(e) => setWarranty(e.target.value)} /></label>
      </div>
      <span style={{ fontSize: 12, color: "var(--muted)" }}>The dates the email states, as the app read them. Change one if the store says otherwise; each comes back as a reminder.</span>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button className="btn dark" type="button" disabled={busy} onClick={keep}>{busy ? "Keeping…" : "Keep the receipt"}</button>
        <ConfirmButton label="Wrong, discard" question="Discard what was read from this email?" yes="Yes, discard it" no="Keep it" onConfirm={discard} />
        {f.message && <Link to={`/mail?message=${f.message.id}`} style={{ fontSize: 13, marginLeft: "auto" }}>Open the message</Link>}
      </div>
      <Said text={error} tone="error" />
    </div>
  );
}

// Money that moved between your own accounts (M20): two rows the app thinks are one move, or one purchase seen twice.
const SOURCE_WORD: Record<string, string> = { plaid: "your bank link (Plaid)", teller: "your bank link (Teller)", rocketmoney: "the Rocket Money export", csv: "a file you imported", file: "a file you imported", ofx: "a bank file you imported", manual: "a row you typed in", alert: "a bank alert email", era: "Era", demo: "the demo" };
const sourceWord = (side: PairSide | null) => (side ? SOURCE_WORD[side.source] ?? side.source : "");

function PairBox({ side, label }: { side: PairSide | null; label: string }) {
  return (
    <div style={{ minWidth: 0, background: "var(--surface-2)", borderRadius: 10, padding: "8px 10px", display: "flex", flexDirection: "column", gap: 2 }}>
      <span className="lab">{label}</span>
      {side ? (
        <>
          <strong style={{ fontSize: 14, overflowWrap: "anywhere" }}>{side.account || "An account"}</strong>
          <span className="num" style={{ fontSize: 16, fontWeight: 800 }}>{money(Math.abs(side.amount), { cents: true })}</span>
          <span style={{ fontSize: 12, color: "var(--ink-2)", overflowWrap: "anywhere" }}>{shortDate(side.date)} · {side.merchant}</span>
        </>
      ) : (
        <span style={{ fontSize: 12, color: "var(--muted)" }}>This row is no longer in your transactions.</span>
      )}
    </div>
  );
}

// The two sides as a picture: out of one account, an arrow, into the other; or the same purchase twice.
function PairPicture({ p }: { p: PairRow }) {
  const dup = p.kind === "duplicate";
  const labels = dup ? [`Seen from ${sourceWord(p.out)}`, `Seen again from ${sourceWord(p.in)}`] : p.kind === "card_payment" ? ["Paid out of", "Paid onto the card"] : ["Out of", "Into"];
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto minmax(0, 1fr)", gap: 8, alignItems: "center" }}>
      <PairBox side={p.out} label={labels[0]} />
      <span style={{ display: "inline-flex", flexDirection: "column", alignItems: "center", gap: 2, fontSize: 11, fontWeight: 700, color: "var(--ink-2)", textAlign: "center" }}>
        {dup ? <Copy size={20} aria-hidden="true" /> : <ArrowRight size={20} aria-hidden="true" />}
        {dup ? "seen twice" : "moved"}
      </span>
      <PairBox side={p.in} label={labels[1]} />
    </div>
  );
}

function pairDone(p: PairRow, decision: "yes" | "no"): string {
  if (p.kind === "duplicate") return decision === "yes" ? "The second one is hidden, so the purchase counts once." : "Both kept as real purchases. The app will not ask about these two again.";
  if (decision === "yes") return `Marked as the same money${p.out && p.in ? `, ${p.out.account} to ${p.in.account}` : ""}. It no longer counts as spending or income.`;
  return "Kept apart. The app will not ask about these two again.";
}

function PairCard({ p, onDone }: { p: PairRow; onDone: (text: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dup = p.kind === "duplicate";

  async function decide(decision: "yes" | "no") {
    setBusy(true);
    setError(null);
    try {
      await api<PairRow>(`/api/v1/pairs/${p.id}`, { method: "POST", body: JSON.stringify({ decision }) });
      onDone(pairDone(p, decision));
    } catch (e) {
      setError(`Couldn't save your answer: ${(e as Error).message}`);
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
      <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>{p.title}</h3>
      <PairPicture p={p} />
      <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Why the app paired them: {p.reason}. {Math.round(p.confidence * 100)}% sure.</div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button className="btn dark" type="button" disabled={busy} onClick={() => decide("yes")}>{dup ? "Yes, hide the second one" : "Yes, they are the same money"}</button>
        <button className="btn" type="button" disabled={busy} onClick={() => decide("no")}>{dup ? "No, both are real" : "No, keep them apart"}</button>
      </div>
      <Said text={error} tone="error" />
    </div>
  );
}

function Pairs({ state, onChanged }: { state: Loaded<PairsOut>; onChanged: () => void }) {
  const [said, setSaid] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  if (state.error && !state.data) return <Failed error={state.error} what="money that moved between your accounts" onRetry={state.reload} />;
  if (!state.data) return <Loading what="money that moved between your accounts" />;
  const { waiting, matched, counts } = state.data;
  if (waiting.length === 0 && matched.length === 0 && !said) return null;
  const recent = matched.slice(0, 20);
  const done = (text: string) => {
    setSaid(text);
    state.reload();
    onChanged();
  };
  return (
    <>
      <h2 className="lab" style={{ margin: "6px 0 0" }}>Money that moved between your accounts · {counts.waiting}</h2>
      <Said text={said} />
      {waiting.length === 0 && <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Nothing to decide here.</div>}
      {waiting.map((p) => <PairCard key={p.id} p={p} onDone={done} />)}
      {recent.length > 0 && (
        <details onToggle={(e) => setOpen(e.currentTarget.open)} className="card" style={{ padding: "10px 16px", fontSize: 13 }}>
          <summary style={{ cursor: "pointer", fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 4, color: "var(--ink-2)", minHeight: 32 }}>
            <ChevronRight size={14} aria-hidden style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform .15s" }} />
            Matched automatically · {counts.matched}{counts.matched > recent.length ? ` (the newest ${recent.length} shown)` : ""}
          </summary>
          <div style={{ display: "flex", flexDirection: "column", marginTop: 6 }}>
            {recent.map((p, i) => (
              <div key={p.id} style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", padding: "8px 0", borderTop: i === 0 ? 0 : "1px solid var(--surface-2)" }}>
                <div style={{ flex: "1 1 240px", minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
                  <span style={{ fontWeight: 700 }}>{p.title}</span>
                  <span style={{ color: "var(--ink-2)", display: "inline-flex", alignItems: "center", gap: 4, flexWrap: "wrap" }}>
                    {p.kind === "duplicate" ? (
                      <>{p.out?.account || "An account"}, from {sourceWord(p.out)} and {sourceWord(p.in)}</>
                    ) : (
                      <>{p.out?.account || "An account"} <ArrowRight size={13} aria-label="to" /> {p.in?.account || "an account"}</>
                    )}
                    {p.out && <span className="num"> · {money(Math.abs(p.out.amount), { cents: true })} on {shortDate(p.out.date)}</span>}
                  </span>
                  <span style={{ fontSize: 12, color: "var(--muted)" }}>{p.status === "confirmed" ? "You said yes" : "Matched by the app"}{p.decided_at ? `, ${when(p.decided_at)}` : ""}</span>
                </div>
                <ConfirmButton
                  label="Undo"
                  style={{ minHeight: 32, padding: "4px 10px" }}
                  question={p.kind === "duplicate" ? "Undo? The hidden row shows again and both count." : "Undo this match? Both rows go back to how they were, and the app will not match them again."}
                  yes="Yes, undo it"
                  no="Keep the match"
                  onConfirm={async () => {
                    await api<PairRow>(`/api/v1/pairs/${p.id}`, { method: "POST", body: JSON.stringify({ decision: "no" }) });
                    done(p.kind === "duplicate" ? "Undone: both rows count again." : "Undone: both rows are back as they were.");
                  }}
                />
              </div>
            ))}
          </div>
        </details>
      )}
    </>
  );
}

const CADENCE: Record<string, string> = { weekly: "every week", biweekly: "every two weeks", semimonthly: "twice a month", monthly: "monthly", quarterly: "every three months", semiannual: "every six months", yearly: "yearly" };
const LEFT_HINT = "Type how many payments are left, like 4. The lender's app shows it.";

/** What naming a charge made, from the server's answer, with the place it landed. */
interface Classified {
  bill?: { name: string; amount: number; cadence: string };
  income?: { name: string; cadence: string; expected_net: number; next_date: string };
  installment_plan?: { merchant: string; installment: number; remaining: number; next_date: string };
}

function classifyDone(s: SeriesRow, kind: string, r: Classified): Done {
  if (r.bill) return { text: `Added ${r.bill.name}, ${money(r.bill.amount, { cents: true })} ${CADENCE[r.bill.cadence] ?? r.bill.cadence}, to your bills list as a ${kind === "subscription" ? "subscription" : "bill"}.`, to: "/bills", link: "See it on Bills" };
  if (r.income) return { text: `Added ${r.income.name} as a paycheck: ${money(r.income.expected_net)} ${CADENCE[r.income.cadence] ?? r.income.cadence}, the next on ${shortDate(r.income.next_date)}.`, to: "/paystubs#paycheck", link: "See your paychecks" };
  if (r.installment_plan) {
    const p = r.installment_plan;
    return { text: `Added the pay-later plan ${p.merchant}: ${money(p.installment, { cents: true })} each, ${p.remaining} payment${p.remaining === 1 ? "" : "s"} left, the next on ${shortDate(p.next_date)}.`, to: "/borrowing#installments", link: "See it on Borrowing" };
  }
  return { text: s.kind === "income" ? `${s.merchant} is marked as not income. It will not be asked about again.` : `${s.merchant} is marked as regular spending, not a bill. It will not be asked about again.` };
}

function Row({ s, onDone }: { s: SeriesRow; onDone: (done: Done) => void }) {
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
    // A pay-later plan needs how many payments are left; without it the plan would end after one more payment.
    const left = Number(remaining.trim());
    if (kind === "installment" && !(/^\d+$/.test(remaining.trim()) && left >= 1)) {
      setError(LEFT_HINT);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await api<Classified>(`/api/v1/recurring/${s.id}/classify`, { method: "POST", body: JSON.stringify({ kind, autopay, remaining: kind === "installment" ? left : undefined }) });
      onDone(classifyDone(s, kind, r));
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
              <button className={hint === "installment" ? "btn dark" : "btn"} type="button" disabled={busy} onClick={() => classify("installment")}>Pay-later plan{mark("installment")}</button>
              <label style={{ display: "inline-flex", flexDirection: "column", gap: 2 }} title="How many payments are left; the lender's app shows it">
                <span className="lab">Payments left (for a plan)</span>
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
  const pairs = useApi<PairsOut>("/api/v1/pairs");
  // The count is the engine's (what the tab badge reads too), asked again after every answer here.
  const snap = useApi<Snapshot>("/api/v1/snapshot", { live: true });
  const [approvalsWaiting, setApprovalsWaiting] = useState<number | null>(null);
  const [detecting, setDetecting] = useState(false);
  const [looked, setLooked] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  const [factSaid, setFactSaid] = useState<Done | null>(null);
  const [seriesSaid, setSeriesSaid] = useState<Done | null>(null);
  // Your own emails waiting are counted from the list below, as the tab badge does; the rest is the engine's count.
  const total = snap.data && approvalsWaiting !== null ? snap.data.pending.confirmations + approvalsWaiting : null;

  async function detect() {
    setDetecting(true);
    setLooked(null);
    try {
      const r = await api<{ created: number; linked: number }>("/api/v1/recurring/detect", { method: "POST" });
      const found = [r.created ? `${r.created} new repeating charge${r.created === 1 ? "" : "s"}` : "", r.linked ? `${r.linked} matched to your bills` : ""].filter(Boolean).join(", ");
      setLooked({ tone: "info", text: found ? `Looked again: ${found}.` : "Looked again. Nothing new found." });
      reload();
      facts.reload();
      pairs.reload();
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
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Needs you{total !== null ? ` · ${total}` : ""}</h1>
        <div style={{ flexGrow: 1 }} />
        <Said text={looked?.text ?? null} tone={looked?.tone} />
        <button className="btn" type="button" onClick={detect} disabled={detecting}>{detecting ? "Looking…" : "Look again"}</button>
      </div>
      <p style={{ margin: 0, color: "var(--ink-2)", fontSize: 13 }}>First: emails waiting for your OK. Then: details read from your mail and from documents you added. Then: money that moved between your accounts. Then: repeating charges found in your transactions. Once you answer one, every screen uses it.</p>
      <Approvals onChanged={onChanged} onCount={setApprovalsWaiting} />
      {facts.error && !facts.data && <Failed error={facts.error} what="what was read from your mail and documents" onRetry={facts.reload} />}
      {facts.data && facts.data.facts.length > 0 && (
        <>
          <h2 className="lab" style={{ margin: 0 }}>From your mail and documents · {facts.data.pending}</h2>
          {facts.data.facts.map((f) =>
            f.kind === "receipt" ? (
              <ReceiptCard
                key={f.id}
                f={f as FactWithCandidates}
                onDone={(done) => {
                  setFactSaid(done);
                  facts.reload();
                  onChanged();
                }}
              />
            ) : (
              <FactCard
                key={f.id}
                f={f}
                onDone={(done) => {
                  setFactSaid(done);
                  facts.reload();
                  onChanged();
                }}
              />
            ),
          )}
        </>
      )}
      <DoneLine done={factSaid} />
      <Pairs state={pairs} onChanged={onChanged} />
      {error && !data && <Failed error={error} what="the repeating charges" onRetry={reload} />}
      {!data && !error && <Loading what="what needs you" />}
      {data && data.series.length > 0 && <h2 className="lab" style={{ margin: "6px 0 0" }}>From your transactions · {data.pending}</h2>}
      <DoneLine done={seriesSaid} />
      {data && data.series.length === 0 && facts.data && facts.data.facts.length === 0 && pairs.data && pairs.data.waiting.length === 0 && approvalsWaiting === 0 && (
        <Empty to="/today" action="Back to Today">Nothing needs you. No email is waiting for your OK, every repeating charge in your transactions has a name, the mail has nothing new, and no money between your accounts is waiting for a yes or no.</Empty>
      )}
      {data?.series.map((s) => (
        <Row
          key={s.id}
          s={s}
          onDone={(done) => {
            setSeriesSaid(done);
            reload();
            onChanged();
          }}
        />
      ))}
    </div>
  );
}
