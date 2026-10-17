// Import a file from any bank or vendor: a look at what it holds first, then the import under an account.
// A bills list (name, amount, how often, due day) is told apart from transactions and lands on your bills list.
// A statement PDF with a text layer is read line by line (M21); a card statement is read the card's way round.
import { OctagonAlert } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { money, shortDate } from "../format";
import { ConfirmButton } from "./ConfirmButton";
import { Said } from "./State";
import { Where } from "./Where";

interface SampleRow {
  date?: string;
  amount?: number;
  description?: string;
  merchant?: string;
  category?: string;
  name?: string;
  cadence?: string;
  due_day?: number | null;
  next_date?: string | null;
  autopay?: boolean;
}

interface Preview {
  /** "affirm": a PDF that is Affirm's Loans Statement. That answer carries only the file name, `affirm` and a note. */
  kind: "csv" | "ofx" | "bills" | "statement" | "affirm";
  filename: string;
  columns?: string[];
  mapping?: Record<string, string | null>;
  sign?: "bank" | "ledger";
  vendor?: string;
  account_type?: string;
  account_id?: string;
  total: number;
  sample: SampleRow[];
  positions?: { name: string; ticker: string; value: number }[] | null;
  ready: boolean;
  skipped?: number;
  year?: number;
  note?: string;
  /** True when the PDF is Affirm's Loans Statement, as the server read it. */
  affirm?: boolean;
}

const isPdf = (f: File) => f.type === "application/pdf" || /\.pdf$/i.test(f.name);
/** Affirm's Loans Statement is read on Borrowing, loan by loan, not as bank lines. The server says so from the
 * statement's own words, never from the file's name. */
const isAffirm = (p: Preview) => p.kind === "affirm" || p.affirm === true;
const lines = (n: number) => `${n} ${n === 1 ? "line" : "lines"}`;

export function FileImport({ onDone }: { onDone?: (summary: string) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [account, setAccount] = useState("");
  const [institution, setInstitution] = useState("");
  const [kind, setKind] = useState("checking");
  const [sign, setSign] = useState<"bank" | "ledger">("bank");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [into, setInto] = useState<string>("new");
  const [last, setLast] = useState<{ id: number; summary: string } | null>(null);
  const [done, setDone] = useState<string | null>(null);
  // The last thing done was reading an Affirm statement, so its result links to Borrowing.
  const [doneAffirm, setDoneAffirm] = useState(false);
  const accounts = useApi<{ accounts: { id: number; name: string; kind: string; institution: string; active?: boolean }[] }>("/api/v1/accounts");

  // A PDF is read with the kind it is (a card statement reads the other way round), so the kind goes with it.
  async function look(f: File, as: string = kind) {
    setFile(f);
    setPreview(null);
    setError(null);
    setDone(null);
    setBusy(true);
    try {
      const body = new FormData();
      body.append("file", f);
      if (isPdf(f)) body.append("kind", as);
      const p = await api<Preview>("/api/v1/imports/preview", { method: "POST", body });
      // An Affirm answer has no rows to show: it is offered on Borrowing, and nothing below reads its lines.
      setPreview(isAffirm(p) ? { ...p, total: 0, ready: false, sample: [] } : p);
      if (p.sign) setSign(p.sign);
      if (p.kind === "ofx") {
        setInstitution(p.vendor ?? "");
        setAccount(p.vendor ? `${p.vendor} ${p.account_type || "account"}` : "");
        if (p.account_type === "creditcard") setKind("card");
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  // The kind of account and how a statement is read go together: a card statement is read again when it changes.
  function pickKind(next: string) {
    const turned = (kind === "card") !== (next === "card");
    setKind(next);
    if (turned && file && preview?.kind === "statement") look(file, next);
  }

  // An Affirm Loans Statement goes where it is read loan by loan, with the lender's balances and rates.
  async function readAffirm() {
    if (!file) return;
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const r = await api<{ loans: number; created: number; updated: number; retired: number }>("/api/v1/borrowing/statement", { method: "POST", body });
      const summary = `${file.name}: ${r.loans} pay-later loans read from the Affirm statement; ${r.created} new, ${r.updated} updated, ${r.retired} closed. Borrowing shows each one.`;
      onDone?.(summary);
      setDone(summary);
      setDoneAffirm(true);
      setPreview(null);
      setFile(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function run() {
    if (!file) return;
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const body = new FormData();
      body.append("file", file);
      body.append("account_name", account);
      body.append("institution", institution);
      body.append("kind", kind);
      body.append("sign", sign);
      if (into !== "new") body.append("account_id", into);
      const r = await api<{ summary: string; inserted: number; import_id: number | null }>("/api/v1/imports/file", { method: "POST", body });
      const summary = `${file.name}: ${r.summary}`;
      onDone?.(summary);
      setDone(summary);
      setDoneAffirm(false);
      setLast(r.import_id ? { id: r.import_id, summary } : null);
      setPreview(null);
      setFile(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // Asked first by ConfirmButton; a failure is said beside it.
  async function undoLast() {
    if (!last) return;
    const r = await api<{ removed: number }>(`/api/v1/imports/${last.id}/undo`, { method: "POST" });
    const summary = `Undone: ${r.removed} rows from ${last.summary.split(":")[0]} removed; everything else stays.`;
    onDone?.(summary);
    setDone(summary);
    setDoneAffirm(false);
    setLast(null);
  }

  const affirm = preview !== null && isAffirm(preview);
  const needsName = preview !== null && preview.kind !== "bills" && !affirm && into === "new" && !account.trim();
  // One amount column: its sign says which way the money went, and the choice below can flip it. Separate money-out
  // and money-in columns say it themselves.
  const oneColumn = preview?.kind === "csv" && Boolean(preview.mapping?.amount) && !(preview.mapping?.debit && preview.mapping?.credit);
  // The sample comes back read with the sign the file seemed to use (positive is money out); a different choice
  // shows it the other way round, for the preview only. The import reads the file again with the choice.
  const flip = oneColumn && preview?.sign !== undefined && sign !== preview.sign ? -1 : 1;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {done && <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "baseline" }}><Said text={done} />{doneAffirm && <Link to="/borrowing#loans" style={{ fontSize: 12, fontWeight: 700 }}>Open Borrowing</Link>}</div>}
      {last && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 13 }}>
          <span>Not what you meant?</span>
          <ConfirmButton label="Undo this import" question="Take out every row this import added? Everything else stays." yes="Yes, undo it" no="Keep it" onConfirm={undoLast} />
          <Link to="/settings/backups" style={{ fontSize: 12 }}>Every import and copy</Link>
        </div>
      )}
      <label style={{ display: "flex", flexDirection: "column", gap: 4, alignItems: "flex-start" }}>
        <span className="lab">File to import (CSV, OFX, QFX, QBO, or a statement PDF)</span>
        <input className="input" type="file" accept=".csv,.ofx,.qfx,.qbo,.pdf,text/csv,application/x-ofx,application/pdf" onChange={(e) => e.target.files?.[0] && look(e.target.files[0])} />
      </label>
      <Where id="bank_file" />
      {busy && !preview && <span role="status" style={{ fontSize: 13, color: "var(--ink-2)" }}>Reading the file…</span>}
      {error && <Said text={error} tone="error" />}
      {preview && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, borderTop: "1px solid var(--surface-2)", paddingTop: 8 }}>
          <div style={{ fontSize: 13 }}>
            <strong>{preview.filename}</strong> · {affirm ? "an Affirm Loans Statement PDF" : preview.kind === "statement" ? `a statement PDF · Read ${lines(preview.total)} from the statement (${preview.skipped ?? 0} skipped)` : `${preview.kind === "ofx" ? `a statement from ${preview.vendor || "a vendor"}` : preview.kind === "bills" ? "a bills list" : "a CSV"} · ${preview.total} rows`}
            {preview.kind === "csv" && preview.mapping && (
              <span style={{ color: "var(--ink-2)" }}> · date in <code>{preview.mapping.date ?? "?"}</code>, amount in <code>{preview.mapping.amount ?? `${preview.mapping.debit ?? "?"} / ${preview.mapping.credit ?? "?"}`}</code>, description in <code>{preview.mapping.description ?? "?"}</code></span>
            )}
          </div>
          {!preview.ready && !affirm && (
            <div role="alert" style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 13, color: "var(--critical-ink)", fontWeight: 700 }}>
              <OctagonAlert size={14} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2 }} />
              <span>{preview.kind === "statement" ? "No line with a date, words and an amount was found in this statement; it cannot be imported as it is. A CSV of the same months from the bank's website will work." : "No date and amount columns were found; this file cannot be imported as it is."}</span>
            </div>
          )}
          {preview.kind === "bills" && preview.sample.length > 0 && (
            <div style={{ overflowX: "auto" }}>
              <table className="ledger" style={{ fontSize: 12 }}>
                <thead><tr><th>Bill</th><th>Every</th><th>Due day</th><th>Autopay</th><th style={{ textAlign: "right" }}>Amount</th></tr></thead>
                <tbody>{preview.sample.map((r, i) => <tr key={i}><td>{r.name}</td><td>{r.cadence}</td><td>{r.due_day ?? "none (skipped)"}</td><td>{r.autopay ? "yes" : "no"}</td><td className="num" style={{ textAlign: "right" }}>{money(r.amount ?? 0, { cents: true })}</td></tr>)}</tbody>
              </table>
            </div>
          )}
          {preview.kind === "statement" && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {(preview.total > 0 || (preview.skipped ?? 0) > 0) && (
                <div aria-hidden="true" style={{ display: "flex", height: 10, borderRadius: 5, overflow: "hidden", background: "var(--surface-2)", maxWidth: 420 }}>
                  <div style={{ flex: `${preview.total} 1 0`, background: "var(--paycheck)" }} />
                  <div style={{ flex: `${preview.skipped ?? 0} 1 0`, background: "var(--buffer)" }} />
                </div>
              )}
              <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12, color: "var(--ink-2)" }}>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span className="sw" style={{ background: "var(--paycheck)", margin: 0 }} />Read: {lines(preview.total)}</span>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span className="sw" style={{ background: "var(--buffer)", margin: 0 }} />Skipped (totals, balances, page lines): {preview.skipped ?? 0}</span>
              </div>
              <label style={{ display: "inline-flex", alignItems: "center", gap: 8, minHeight: 32, fontSize: 13, fontWeight: 700 }}>
                <input type="checkbox" checked={kind === "card"} disabled={busy} onChange={(e) => pickKind(e.target.checked ? "card" : "checking")} style={{ width: 18, height: 18 }} />
                This is a card statement
              </label>
              <span style={{ fontSize: 12, color: "var(--ink-2)" }}>On a card statement only payments, credits and refunds are money in; every other line is a charge. Ticking or unticking this reads the file again.</span>
            </div>
          )}
          {preview.kind === "statement" && preview.sample.length > 0 && (
            <div style={{ overflowX: "auto" }}>
              <table className="ledger" style={{ fontSize: 12 }}>
                <thead><tr><th>Date</th><th>Description</th><th style={{ textAlign: "right" }}>Money out</th><th style={{ textAlign: "right" }}>Money in</th></tr></thead>
                <tbody>
                  {preview.sample.map((r, i) => {
                    const into = (r.amount ?? 0) < 0;
                    return (
                      <tr key={i}>
                        <td style={{ whiteSpace: "nowrap" }}>{r.date ? shortDate(r.date) : "no date"}</td>
                        <td>{r.description}</td>
                        <td className="num" style={{ textAlign: "right" }}>{into ? "" : money(r.amount ?? 0, { cents: true })}</td>
                        <td className="num" style={{ textAlign: "right", color: "var(--safe-ink)", fontWeight: 700 }}>{into ? money(Math.abs(r.amount ?? 0), { cents: true, sign: true }) : ""}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {preview.kind === "statement" && (
            <div style={{ fontSize: 12, color: "var(--ink-2)", display: "flex", flexDirection: "column", gap: 2 }}>
              {preview.sample.length > 0 && preview.sample.length < preview.total && <span>These are the first {lines(preview.sample.length)} of {preview.total}.</span>}
              {preview.year ? <span>Dates printed without a year are read as {preview.year}; check them above.</span> : null}
              {preview.note && <span>{preview.note}</span>}
            </div>
          )}
          {affirm && (
            <div role="status" style={{ display: "flex", flexDirection: "column", gap: 6, padding: "8px 10px", borderRadius: 10, background: "var(--surface-2)", fontSize: 13 }}>
              <span><strong>This is an Affirm Loans Statement.</strong> It is read loan by loan, with the lender's balances and rates, the way <Link to="/borrowing#loans">Borrowing</Link> reads it; its lines are not bank transactions, so they are not imported here.</span>
              <span style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                <button className="btn dark" type="button" disabled={busy} onClick={readAffirm}>{busy ? "Reading…" : "Read it as an Affirm statement"}</button>
                <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Every loan it lists lands on Borrowing and Debts.</span>
              </span>
            </div>
          )}
          {preview.kind !== "bills" && preview.kind !== "statement" && !affirm && preview.sample.length > 0 && (
            <div style={{ overflowX: "auto" }}>
              <table className="ledger" style={{ fontSize: 12 }}>
                <thead><tr><th>Date</th><th>Description</th><th style={{ textAlign: "right" }}>Money out</th><th style={{ textAlign: "right" }}>Money in</th></tr></thead>
                <tbody>
                  {preview.sample.map((r, i) => {
                    const shown = (r.amount ?? 0) * flip;
                    const into = shown < 0;
                    return (
                      <tr key={i}>
                        <td style={{ whiteSpace: "nowrap" }}>{r.date ? shortDate(r.date) : "no date"}</td>
                        <td>{r.description}</td>
                        <td className="num" style={{ textAlign: "right" }}>{into ? "" : money(shown, { cents: true })}</td>
                        <td className="num" style={{ textAlign: "right", color: "var(--safe-ink)", fontWeight: 700 }}>{into ? money(Math.abs(shown), { cents: true, sign: true }) : ""}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {oneColumn && preview.sample.length > 0 && <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Check a paycheck and a purchase above: a paycheck belongs under Money in. If they are the wrong way round, change "Signs in the file" below; the rows above follow.</span>}
          {preview.kind !== "bills" && !affirm && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(160px, 100%), 1fr))", gap: 8 }}>
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Into</span>
              <select className="input" value={into} onChange={(e) => setInto(e.target.value)}>
                <option value="new">A new account, or the one with this name</option>
                {(accounts.data?.accounts ?? []).filter((a) => a.active !== false).map((a) => <option key={a.id} value={String(a.id)}>{a.name}{a.institution ? ` · ${a.institution}` : ""}</option>)}
              </select>
            </label>
            {into === "new" && <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Account name</span><input className="input" value={account} onChange={(e) => setAccount(e.target.value)} placeholder="Everyday Checking" /></label>}
            {into === "new" && <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">At</span><input className="input" value={institution} onChange={(e) => setInstitution(e.target.value)} placeholder="Example Bank" /></label>}
            {into === "new" && <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Kind</span><select className="input" value={kind} onChange={(e) => pickKind(e.target.value)}><option value="checking">checking</option><option value="savings">savings</option><option value="card">card</option><option value="loan">loan</option></select></label>}
            {oneColumn && (
              <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Signs in the file</span><select className="input" value={sign} onChange={(e) => setSign(e.target.value as "bank" | "ledger")}><option value="bank">money in is positive (most banks)</option><option value="ledger">money out is positive</option></select></label>
            )}
          </div>
          )}
          {!affirm && (
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button className="btn dark" type="button" disabled={busy || !preview.ready || needsName} onClick={run}>{busy ? "Importing…" : preview.kind === "bills" ? "Put these bills on your bills list" : "Import these rows"}</button>
            {needsName && <span style={{ fontSize: 13, fontWeight: 700, color: "var(--ink)" }}>Name the account first.</span>}
            <span style={{ fontSize: 12, color: "var(--ink-2)", flex: "1 1 260px" }}>{preview.kind === "bills" ? "A bill with the same name is updated, not doubled; a row without a due day is skipped. A copy of everything is kept first, and the import can be undone." : "A row the account already holds, from an earlier file, a bank link or an export, is not added again. A copy of everything is kept first, and the import can be undone."}</span>
          </div>
          )}
        </div>
      )}
    </div>
  );
}
