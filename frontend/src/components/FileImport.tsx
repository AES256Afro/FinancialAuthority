// Import a file from any bank or vendor: a look at what it holds first, then the import under an account.
// A bills list (name, amount, how often, due day) is told apart from transactions and lands on your bills list.
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
  category?: string;
  name?: string;
  cadence?: string;
  due_day?: number | null;
  next_date?: string | null;
  autopay?: boolean;
}

interface Preview {
  kind: "csv" | "ofx" | "bills";
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
}

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
  const accounts = useApi<{ accounts: { id: number; name: string; kind: string; institution: string; active?: boolean }[] }>("/api/v1/accounts");

  async function look(f: File) {
    setFile(f);
    setPreview(null);
    setError(null);
    setDone(null);
    setBusy(true);
    try {
      const body = new FormData();
      body.append("file", f);
      const p = await api<Preview>("/api/v1/imports/preview", { method: "POST", body });
      setPreview(p);
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
    setLast(null);
  }

  const needsName = preview !== null && preview.kind !== "bills" && into === "new" && !account.trim();
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {done && <Said text={done} />}
      {last && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 13 }}>
          <span>Not what you meant?</span>
          <ConfirmButton label="Undo this import" question="Take out every row this import added? Everything else stays." yes="Yes, undo it" no="Keep it" onConfirm={undoLast} />
          <Link to="/settings/backups" style={{ fontSize: 12 }}>Every import and copy</Link>
        </div>
      )}
      <label style={{ display: "flex", flexDirection: "column", gap: 4, alignItems: "flex-start" }}>
        <span className="lab">File to import (CSV, OFX, QFX or QBO)</span>
        <input className="input" type="file" accept=".csv,.ofx,.qfx,.qbo,text/csv,application/x-ofx" onChange={(e) => e.target.files?.[0] && look(e.target.files[0])} />
      </label>
      <Where id="bank_file" />
      {busy && !preview && <span role="status" style={{ fontSize: 13, color: "var(--ink-2)" }}>Reading the file…</span>}
      {error && <Said text={error} tone="error" />}
      {preview && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, borderTop: "1px solid var(--surface-2)", paddingTop: 8 }}>
          <div style={{ fontSize: 13 }}>
            <strong>{preview.filename}</strong> · {preview.kind === "ofx" ? `a statement from ${preview.vendor || "a vendor"}` : preview.kind === "bills" ? "a bills list" : "a CSV"} · {preview.total} rows
            {preview.kind === "csv" && preview.mapping && (
              <span style={{ color: "var(--ink-2)" }}> · date in <code>{preview.mapping.date ?? "?"}</code>, amount in <code>{preview.mapping.amount ?? `${preview.mapping.debit ?? "?"} / ${preview.mapping.credit ?? "?"}`}</code>, description in <code>{preview.mapping.description ?? "?"}</code></span>
            )}
          </div>
          {!preview.ready && (
            <div role="alert" style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 13, color: "var(--critical-ink)", fontWeight: 700 }}>
              <OctagonAlert size={14} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2 }} />
              <span>No date and amount columns were found; this file cannot be imported as it is.</span>
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
          {preview.kind !== "bills" && preview.sample.length > 0 && (
            <div style={{ overflowX: "auto" }}>
              <table className="ledger" style={{ fontSize: 12 }}>
                <thead><tr><th>Date</th><th>Description</th><th style={{ textAlign: "right" }}>Amount</th></tr></thead>
                <tbody>{preview.sample.map((r, i) => <tr key={i}><td style={{ whiteSpace: "nowrap" }}>{r.date ? shortDate(r.date) : "no date"}</td><td>{r.description}</td><td className="num" style={{ textAlign: "right" }}>{money(r.amount ?? 0, { cents: true })}</td></tr>)}</tbody>
              </table>
            </div>
          )}
          {preview.kind !== "bills" && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(160px, 100%), 1fr))", gap: 8 }}>
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Into</span>
              <select className="input" value={into} onChange={(e) => setInto(e.target.value)}>
                <option value="new">A new account, or the one with this name</option>
                {(accounts.data?.accounts ?? []).filter((a) => a.active !== false).map((a) => <option key={a.id} value={String(a.id)}>{a.name}{a.institution ? ` · ${a.institution}` : ""}</option>)}
              </select>
            </label>
            {into === "new" && <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Account name</span><input className="input" value={account} onChange={(e) => setAccount(e.target.value)} placeholder="Everyday Checking" /></label>}
            {into === "new" && <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">At</span><input className="input" value={institution} onChange={(e) => setInstitution(e.target.value)} placeholder="Example Bank" /></label>}
            {into === "new" && <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Kind</span><select className="input" value={kind} onChange={(e) => setKind(e.target.value)}><option value="checking">checking</option><option value="savings">savings</option><option value="card">card</option><option value="loan">loan</option></select></label>}
            {preview.kind === "csv" && (
              <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Signs in the file</span><select className="input" value={sign} onChange={(e) => setSign(e.target.value as "bank" | "ledger")}><option value="bank">money in is positive (most banks)</option><option value="ledger">money out is positive</option></select></label>
            )}
          </div>
          )}
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button className="btn dark" type="button" disabled={busy || !preview.ready || needsName} onClick={run}>{busy ? "Importing…" : preview.kind === "bills" ? "Put these bills on your bills list" : "Import these rows"}</button>
            {needsName && <span style={{ fontSize: 13, fontWeight: 700, color: "var(--ink)" }}>Name the account first.</span>}
            <span style={{ fontSize: 12, color: "var(--ink-2)", flex: "1 1 260px" }}>{preview.kind === "bills" ? "A bill with the same name is updated, not doubled; a row without a due day is skipped. A copy of everything is kept first, and the import can be undone." : "A row the account already holds, from an earlier file, a bank link or an export, is not added again. A copy of everything is kept first, and the import can be undone."}</span>
          </div>
        </div>
      )}
    </div>
  );
}
