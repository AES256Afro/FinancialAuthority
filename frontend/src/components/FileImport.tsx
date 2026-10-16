// Import a file from any bank or vendor: a look at what it holds first, then the import under an account.
// A bills list (name, amount, how often, due day) is told apart from transactions and lands on the Bills register.
import { Undo2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { money, shortDate } from "../format";

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

export function FileImport({ onDone }: { onDone: (summary: string) => void }) {
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
  const accounts = useApi<{ accounts: { id: number; name: string; kind: string; institution: string; active?: boolean }[] }>("/api/v1/accounts");

  async function look(f: File) {
    setFile(f);
    setPreview(null);
    setError(null);
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
    try {
      const body = new FormData();
      body.append("file", file);
      body.append("account_name", account);
      body.append("institution", institution);
      body.append("kind", kind);
      body.append("sign", sign);
      if (into !== "new") body.append("account_id", into);
      const r = await api<{ summary: string; inserted: number; import_id: number | null }>("/api/v1/imports/file", { method: "POST", body });
      onDone(`${file.name}: ${r.summary}`);
      setLast(r.import_id ? { id: r.import_id, summary: `${file.name}: ${r.summary}` } : null);
      setPreview(null);
      setFile(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function undoLast() {
    if (!last) return;
    setBusy(true);
    try {
      const r = await api<{ removed: number }>(`/api/v1/imports/${last.id}/undo`, { method: "POST" });
      onDone(`Undone: ${r.removed} rows from ${last.summary.split(":")[0]} removed; everything else stays.`);
      setLast(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {last && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 13 }}>
          <span>Imported. Not what you meant?</span>
          <button className="btn" type="button" disabled={busy} onClick={undoLast} style={{ display: "inline-flex", gap: 4, alignItems: "center" }}><Undo2 size={14} aria-hidden />Undo this import</button>
          <Link to="/settings/backups" style={{ fontSize: 12 }}>Every import and copy</Link>
        </div>
      )}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <input className="input" type="file" accept=".csv,.ofx,.qfx,.qbo,text/csv,application/x-ofx" onChange={(e) => e.target.files?.[0] && look(e.target.files[0])} aria-label="File to import" />
        <span style={{ fontSize: 12, color: "var(--muted)" }}>CSV from any bank or vendor's download page (Vanguard, Fidelity, Capital One, the credit union), or an OFX, QFX or QBO statement. Or a bills list: a sheet with a name, an amount, how often and the due day or next date, such as Rocket Money's recurring page copied out.</span>
      </div>
      {error && <div style={{ fontSize: 13, color: "var(--critical)" }}>{error}</div>}
      {preview && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, borderTop: "1px solid var(--surface-2)", paddingTop: 8 }}>
          <div style={{ fontSize: 13 }}>
            <strong>{preview.filename}</strong> · {preview.kind === "ofx" ? `a statement from ${preview.vendor || "a vendor"}` : preview.kind === "bills" ? "a bills list" : "a CSV"} · {preview.total} rows
            {preview.kind === "csv" && preview.mapping && (
              <span style={{ color: "var(--ink-2)" }}> · date in <code>{preview.mapping.date ?? "?"}</code>, amount in <code>{preview.mapping.amount ?? `${preview.mapping.debit ?? "?"} / ${preview.mapping.credit ?? "?"}`}</code>, description in <code>{preview.mapping.description ?? "?"}</code></span>
            )}
          </div>
          {!preview.ready && <div style={{ fontSize: 13, color: "var(--critical)" }}>No date and amount columns were found; this file cannot be imported as it is.</div>}
          {preview.kind === "bills" && preview.sample.length > 0 && (
            <table className="ledger" style={{ fontSize: 12 }}>
              <thead><tr><th>Bill</th><th>Every</th><th>Due day</th><th>Autopay</th><th style={{ textAlign: "right" }}>Amount</th></tr></thead>
              <tbody>{preview.sample.map((r, i) => <tr key={i}><td>{r.name}</td><td>{r.cadence}</td><td>{r.due_day ?? "? (skipped)"}</td><td>{r.autopay ? "yes" : "no"}</td><td className="num" style={{ textAlign: "right" }}>{money(r.amount ?? 0, { cents: true })}</td></tr>)}</tbody>
            </table>
          )}
          {preview.kind !== "bills" && preview.sample.length > 0 && (
            <table className="ledger" style={{ fontSize: 12 }}>
              <thead><tr><th>Date</th><th>Description</th><th style={{ textAlign: "right" }}>Amount</th></tr></thead>
              <tbody>{preview.sample.map((r, i) => <tr key={i}><td>{shortDate(r.date ?? "")}</td><td>{r.description}</td><td className="num" style={{ textAlign: "right" }}>{money(r.amount ?? 0, { cents: true })}</td></tr>)}</tbody>
            </table>
          )}
          {preview.kind !== "bills" && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 8 }}>
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
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button className="btn dark" type="button" disabled={busy || !preview.ready || (preview.kind !== "bills" && into === "new" && !account.trim())} onClick={run}>{busy ? "Importing…" : preview.kind === "bills" ? "Put these bills on the register" : "Import these rows"}</button>
            <span style={{ fontSize: 12, color: "var(--muted) " }}>{preview.kind === "bills" ? "A bill with the same name is updated, not doubled; a row without a due day is skipped. A copy of everything is kept first, and the import can be undone." : "A row the account already holds, from an earlier file, a bank link or an export, is not added again. A copy of everything is kept first, and the import can be undone."}</span>
          </div>
        </div>
      )}
    </div>
  );
}
