// Import a file from any bank or vendor: a look at what it holds first, then the import under an account.
import { useState } from "react";
import { api } from "../api";
import { money, shortDate } from "../format";

interface Preview {
  kind: "csv" | "ofx";
  filename: string;
  columns?: string[];
  mapping?: Record<string, string | null>;
  sign?: "bank" | "ledger";
  vendor?: string;
  account_type?: string;
  account_id?: string;
  total: number;
  sample: { date: string; amount: number; description: string; category: string }[];
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
      const r = await api<{ summary: string; inserted: number }>("/api/v1/imports/file", { method: "POST", body });
      onDone(`${file.name}: ${r.summary}`);
      setPreview(null);
      setFile(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <input className="input" type="file" accept=".csv,.ofx,.qfx,.qbo,text/csv,application/x-ofx" onChange={(e) => e.target.files?.[0] && look(e.target.files[0])} aria-label="File to import" />
        <span style={{ fontSize: 12, color: "var(--muted)" }}>CSV from any bank or vendor's download page (Vanguard, Fidelity, Capital One, the credit union), or an OFX, QFX or QBO statement.</span>
      </div>
      {error && <div style={{ fontSize: 13, color: "var(--critical)" }}>{error}</div>}
      {preview && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, borderTop: "1px solid var(--surface-2)", paddingTop: 8 }}>
          <div style={{ fontSize: 13 }}>
            <strong>{preview.filename}</strong> · {preview.kind === "ofx" ? `a statement from ${preview.vendor || "a vendor"}` : "a CSV"} · {preview.total} rows
            {preview.kind === "csv" && preview.mapping && (
              <span style={{ color: "var(--ink-2)" }}> · date in <code>{preview.mapping.date ?? "?"}</code>, amount in <code>{preview.mapping.amount ?? `${preview.mapping.debit ?? "?"} / ${preview.mapping.credit ?? "?"}`}</code>, description in <code>{preview.mapping.description ?? "?"}</code></span>
            )}
          </div>
          {!preview.ready && <div style={{ fontSize: 13, color: "var(--critical)" }}>No date and amount columns were found; this file cannot be imported as it is.</div>}
          {preview.sample.length > 0 && (
            <table className="ledger" style={{ fontSize: 12 }}>
              <thead><tr><th>Date</th><th>Description</th><th style={{ textAlign: "right" }}>Amount</th></tr></thead>
              <tbody>{preview.sample.map((r, i) => <tr key={i}><td>{shortDate(r.date)}</td><td>{r.description}</td><td className="num" style={{ textAlign: "right" }}>{money(r.amount, { cents: true })}</td></tr>)}</tbody>
            </table>
          )}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 8 }}>
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Into the account</span><input className="input" value={account} onChange={(e) => setAccount(e.target.value)} placeholder="Everyday Checking" /></label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">At</span><input className="input" value={institution} onChange={(e) => setInstitution(e.target.value)} placeholder="Example Bank" /></label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Kind</span><select className="input" value={kind} onChange={(e) => setKind(e.target.value)}><option value="checking">checking</option><option value="savings">savings</option><option value="card">card</option><option value="loan">loan</option></select></label>
            {preview.kind === "csv" && (
              <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Signs in the file</span><select className="input" value={sign} onChange={(e) => setSign(e.target.value as "bank" | "ledger")}><option value="bank">money in is positive (most banks)</option><option value="ledger">money out is positive</option></select></label>
            )}
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button className="btn dark" type="button" disabled={busy || !preview.ready || !account.trim()} onClick={run}>{busy ? "Importing…" : "Import these rows"}</button>
            <span style={{ fontSize: 12, color: "var(--muted) " }}>Rows already in the ledger are skipped; nothing is read from the vendor.</span>
          </div>
        </div>
      )}
    </div>
  );
}
